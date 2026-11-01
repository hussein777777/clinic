import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError

INDICATORS = [
    ('wfa', 'Weight-for-age'),
    ('lhfa', 'Length / height-for-age'),
    ('hcfa', 'Head circumference-for-age'),
    ('bmifa', 'BMI-for-age'),
]
# observation type xmlid measured by each indicator
INDICATOR_TYPES = {
    'wfa': 'medical_core.obs_weight',
    'lhfa': 'medical_core.obs_height',
    'hcfa': 'medical_pediatrics.obs_head_circ',
    'bmifa': 'medical_core.obs_bmi',
}
PERCENTILES = [(3, -1.8808), (15, -1.0364), (50, 0.0), (85, 1.0364), (97, 1.8808)]
DAYS_PER_MONTH = 30.4375


def normal_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


class MedicalGrowthReference(models.Model):
    """WHO Child Growth Standards (0-60 months), monthly LMS parameters."""
    _name = 'medical.growth.reference'
    _description = 'WHO Growth Reference (LMS)'
    _order = 'indicator, sex, month'

    indicator = fields.Selection(INDICATORS, required=True, index=True)
    sex = fields.Selection([('male', 'Boys'), ('female', 'Girls')], required=True, index=True)
    month = fields.Integer(required=True)
    l = fields.Float(digits=(10, 5), required=True)  # noqa: E741 - LMS notation
    m = fields.Float(digits=(10, 5), required=True)
    s = fields.Float(digits=(10, 6), required=True)

    @api.model
    def _lms(self, indicator, sex, age_months):
        """LMS parameters at a (fractional) age in months, linearly interpolated."""
        if age_months < 0 or age_months > 60:
            return None
        low, high = math.floor(age_months), math.ceil(age_months)
        rows = self.search([('indicator', '=', indicator), ('sex', '=', sex), ('month', 'in', (low, high))])
        by_month = {r.month: r for r in rows}
        if low not in by_month or high not in by_month:
            return None
        a, b = by_month[low], by_month[high]
        t = age_months - low
        return tuple(getattr(a, k) + (getattr(b, k) - getattr(a, k)) * t for k in ('l', 'm', 's'))

    @api.model
    def _value_at_z(self, lms, z):
        l, m, s = lms  # noqa: E741
        if abs(l) < 1e-9:
            return m * math.exp(s * z)
        return m * (1 + l * s * z) ** (1 / l)

    @api.model
    def _zscore(self, indicator, sex, age_months, value):
        """WHO z-score, with the WHO restriction beyond ±3 SD for weight-based indicators."""
        lms = self._lms(indicator, sex, age_months)
        if not lms or not value:
            return None
        l, m, s = lms  # noqa: E741
        z = math.log(value / m) / s if abs(l) < 1e-9 else ((value / m) ** l - 1) / (l * s)
        if indicator in ('wfa', 'bmifa') and abs(z) > 3:
            sd3 = self._value_at_z(lms, 3 if z > 0 else -3)
            sd2 = self._value_at_z(lms, 2 if z > 0 else -2)
            step = abs(sd3 - sd2)
            z = (3 + (value - sd3) / step) if z > 0 else (-3 + (value - sd3) / step)
        return z

    @api.model
    def _classify(self, indicator, z):
        if z is None:
            return False
        if indicator == 'wfa':
            if z < -3:
                return _("Severely underweight")
            if z < -2:
                return _("Underweight")
            if z > 2:
                return _("Check weight-for-height")
        elif indicator == 'lhfa':
            if z < -3:
                return _("Severely stunted")
            if z < -2:
                return _("Stunted")
            if z > 3:
                return _("Very tall")
        elif indicator == 'bmifa':
            if z < -3:
                return _("Severely wasted")
            if z < -2:
                return _("Wasted")
            if z > 3:
                return _("Obese")
            if z > 2:
                return _("Overweight")
            if z > 1:
                return _("Possible risk of overweight")
        elif indicator == 'hcfa':
            if z < -2:
                return _("Small head: check")
            if z > 2:
                return _("Large head: check")
        return _("Normal")


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_child = fields.Boolean(compute='_compute_is_child')

    @api.depends('birthdate')
    def _compute_is_child(self):
        today = fields.Date.context_today(self)
        for partner in self:
            partner.is_child = bool(partner.birthdate) and (today - partner.birthdate).days < 18 * 365.25

    def get_growth_data(self):
        """Growth chart payload: per indicator, the child's points (age, value, z, percentile) and WHO curves."""
        self.ensure_one()
        self.check_access('read')
        if not self.birthdate or self.gender not in ('male', 'female'):
            return {'ready': False, 'reason': _("Enter the date of birth and gender to see the growth charts.")}
        Reference = self.env['medical.growth.reference']
        Observation = self.env['medical.observation'].sudo()
        result = {'ready': True, 'indicators': []}
        labels = dict(Reference._fields['indicator']._description_selection(self.env))
        for indicator, xmlid in INDICATOR_TYPES.items():
            obs_type = self.env.ref(xmlid, raise_if_not_found=False)
            if not obs_type:
                continue
            points = []
            for obs in Observation.search([('patient_id', '=', self.id), ('type_id', '=', obs_type.id)], order='date'):
                age = (obs.date.date() - self.birthdate).days / DAYS_PER_MONTH
                if age < 0 or age > 60:
                    continue
                z = Reference._zscore(indicator, self.gender, age, obs.value)
                points.append({
                    'age': round(age, 2),
                    'date': fields.Date.to_string(obs.date.date()),
                    'value': obs.value,
                    'z': round(z, 2) if z is not None else None,
                    'percentile': round(100 * normal_cdf(z), 1) if z is not None else None,
                    'status': Reference._classify(indicator, z),
                })
            curves = {str(p): [] for p, _z in PERCENTILES}
            refs = Reference.search([('indicator', '=', indicator), ('sex', '=', self.gender)])
            for ref in refs:
                for p, z in PERCENTILES:
                    curves[str(p)].append({'age': ref.month, 'value': round(Reference._value_at_z((ref.l, ref.m, ref.s), z), 2)})
            result['indicators'].append({
                'key': indicator,
                'name': labels[indicator],
                'unit': obs_type.unit or '',
                'decimals': max(obs_type.decimals, 1),
                'points': points,
                'curves': curves,
            })
        return result


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    head_circumference = fields.Float(string='Head Circumference (cm)', digits=(4, 1),
                                      groups='medical_core.group_medical_nurse')
    is_pediatric = fields.Boolean(compute='_compute_is_pediatric')

    @api.depends('department_id', 'patient_id.birthdate')
    def _compute_is_pediatric(self):
        peds = self.env.ref('medical_pediatrics.dept_pediatrics', raise_if_not_found=False)
        for visit in self:
            visit.is_pediatric = bool(peds) and visit.department_id == peds or visit.patient_id.is_child

    def _sync_vital_observations(self):
        res = super()._sync_vital_observations()
        hc_type = self.env.ref('medical_pediatrics.obs_head_circ', raise_if_not_found=False)
        if not hc_type:
            return res
        Observation = self.env['medical.observation'].sudo()
        for visit in self.sudo():
            current = Observation.search([('visit_id', '=', visit.id), ('type_id', '=', hc_type.id), ('source', '=', 'vitals')])
            if visit.head_circumference:
                vals = {'value': visit.head_circumference, 'date': visit.start}
                if current:
                    current.write(vals)
                else:
                    Observation.create(dict(vals, patient_id=visit.patient_id.id, type_id=hc_type.id,
                                            visit_id=visit.id, source='vitals'))
            elif current:
                current.unlink()
        return res

    def write(self, vals):
        res = super().write(vals)
        if 'head_circumference' in vals:
            self._sync_vital_observations()
        return res

    def _growth_alerts(self):
        """Classification of today's measurements when they are outside normal."""
        self.ensure_one()
        patient = self.patient_id
        if not patient.birthdate or patient.gender not in ('male', 'female') or not self.start:
            return []
        age = (self.start.date() - patient.birthdate).days / DAYS_PER_MONTH
        if age > 60:
            return []
        Reference = self.env['medical.growth.reference']
        visit = self.sudo()
        alerts = []
        labels = dict(Reference._fields['indicator']._description_selection(self.env))
        for indicator, value in (('wfa', visit.weight), ('lhfa', visit.height), ('hcfa', visit.head_circumference),
                                 ('bmifa', visit.bmi)):
            if not value:
                continue
            z = Reference._zscore(indicator, patient.gender, age, value)
            status = Reference._classify(indicator, z)
            if z is not None and status != _("Normal"):
                alerts.append(f"{labels[indicator]}: {status} (z {z:+.1f})")
        return alerts

    def _brief_specialty_facts(self):
        facts = super()._brief_specialty_facts()
        if self.is_pediatric:
            rows = self._growth_alerts()
            due = self.env['medical.vaccination'].sudo().search(
                [('patient_id', '=', self.patient_id.id), ('state', '=', 'planned'),
                 ('due_date', '<=', fields.Date.context_today(self))])
            if due:
                rows.append(_("Vaccines due: %s", ', '.join(due.mapped('display_name'))))
            facts.append((_("Child health"), rows))
        return facts
