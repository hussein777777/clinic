from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

SNELLEN = [
    ('6/5', '6/5'), ('6/6', '6/6'), ('6/7.5', '6/7.5'), ('6/9', '6/9'), ('6/12', '6/12'), ('6/18', '6/18'),
    ('6/24', '6/24'), ('6/36', '6/36'), ('6/60', '6/60'), ('3/60', '3/60'), ('1/60', '1/60'),
    ('CF', 'Counting fingers'), ('HM', 'Hand movements'), ('PL', 'Light perception'), ('NPL', 'No light perception'),
]
LOGMAR = {'6/5': -0.08, '6/6': 0.0, '6/7.5': 0.1, '6/9': 0.18, '6/12': 0.3, '6/18': 0.48, '6/24': 0.6, '6/36': 0.78,
          '6/60': 1.0, '3/60': 1.3, '1/60': 1.78, 'CF': 1.9, 'HM': 2.3, 'PL': 2.7, 'NPL': 3.0}
DOCTOR = 'medical_core.group_medical_doctor'
NURSE = 'medical_core.group_medical_nurse'


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    is_eye = fields.Boolean(compute='_compute_is_eye')
    # visual acuity
    va_od_sc = fields.Selection(SNELLEN, string='VA Right (unaided)', groups=NURSE)
    va_os_sc = fields.Selection(SNELLEN, string='VA Left (unaided)', groups=NURSE)
    va_od_cc = fields.Selection(SNELLEN, string='VA Right (best corrected)', groups=NURSE)
    va_os_cc = fields.Selection(SNELLEN, string='VA Left (best corrected)', groups=NURSE)
    # refraction
    od_sph = fields.Float(string='Right Sphere', digits=(4, 2), groups=NURSE)
    od_cyl = fields.Float(string='Right Cylinder', digits=(4, 2), groups=NURSE)
    od_axis = fields.Integer(string='Right Axis', groups=NURSE)
    od_add = fields.Float(string='Right Add', digits=(4, 2), groups=NURSE)
    os_sph = fields.Float(string='Left Sphere', digits=(4, 2), groups=NURSE)
    os_cyl = fields.Float(string='Left Cylinder', digits=(4, 2), groups=NURSE)
    os_axis = fields.Integer(string='Left Axis', groups=NURSE)
    os_add = fields.Float(string='Left Add', digits=(4, 2), groups=NURSE)
    # pressure and disc
    iop_od = fields.Float(string='IOP Right (mmHg)', digits=(4, 1), groups=NURSE)
    iop_os = fields.Float(string='IOP Left (mmHg)', digits=(4, 1), groups=NURSE)
    cdr_od = fields.Float(string='Cup/Disc Right', digits=(3, 2), groups=DOCTOR)
    cdr_os = fields.Float(string='Cup/Disc Left', digits=(3, 2), groups=DOCTOR)
    # examination
    eye_pupils = fields.Char(string='Pupils', groups=DOCTOR)
    eye_rapd = fields.Boolean(string='RAPD', groups=DOCTOR)
    ant_od = fields.Char(string='Anterior Segment Right', groups=DOCTOR)
    ant_os = fields.Char(string='Anterior Segment Left', groups=DOCTOR)
    fundus_od = fields.Char(string='Fundus Right', groups=DOCTOR)
    fundus_os = fields.Char(string='Fundus Left', groups=DOCTOR)
    eye_prescription_ids = fields.One2many('medical.eye.prescription', 'visit_id', string='Glasses Prescriptions')

    @api.depends('department_id')
    def _compute_is_eye(self):
        eye = self.env.ref('medical_ophthalmology.dept_ophthalmology', raise_if_not_found=False)
        for visit in self:
            visit.is_eye = bool(eye) and visit.department_id == eye

    @api.constrains('od_axis', 'os_axis', 'cdr_od', 'cdr_os')
    def _check_eye_values(self):
        for visit in self.sudo():
            for axis in (visit.od_axis, visit.os_axis):
                if axis and not 0 <= axis <= 180:
                    raise ValidationError(_("The cylinder axis goes from 0 to 180 degrees."))
            for cdr in (visit.cdr_od, visit.cdr_os):
                if cdr and not 0 <= cdr <= 1:
                    raise ValidationError(_("The cup/disc ratio is between 0 and 1."))

    def _eye_observation_values(self):
        """[(observation xmlid, side, value)] typed on this visit."""
        self.ensure_one()
        visit = self.sudo()
        values = [('medical_ophthalmology.obs_iop', 'right', visit.iop_od), ('medical_ophthalmology.obs_iop', 'left', visit.iop_os),
                  ('medical_ophthalmology.obs_cdr', 'right', visit.cdr_od), ('medical_ophthalmology.obs_cdr', 'left', visit.cdr_os)]
        for side, va in (('right', visit.va_od_cc or visit.va_od_sc), ('left', visit.va_os_cc or visit.va_os_sc)):
            values.append(('medical_ophthalmology.obs_va_logmar', side, LOGMAR.get(va) if va else False))
        return values

    def _sync_eye_observations(self):
        Observation = self.env['medical.observation'].sudo()
        for visit in self:
            for xmlid, side, value in visit._eye_observation_values():
                obs_type = self.env.ref(xmlid, raise_if_not_found=False)
                if not obs_type:
                    continue
                current = Observation.search([('visit_id', '=', visit.id), ('type_id', '=', obs_type.id),
                                              ('laterality', '=', side), ('source', '=', 'exam')])
                if value is not False and value is not None and (value or xmlid.endswith('va_logmar')):
                    vals = {'value': value, 'date': visit.start}
                    if current:
                        current.write(vals)
                    else:
                        Observation.create(dict(vals, patient_id=visit.patient_id.id, type_id=obs_type.id,
                                                laterality=side, visit_id=visit.id, source='exam'))
                elif current:
                    current.unlink()

    EYE_TRIGGERS = {'iop_od', 'iop_os', 'cdr_od', 'cdr_os', 'va_od_sc', 'va_os_sc', 'va_od_cc', 'va_os_cc'}

    @api.model_create_multi
    def create(self, vals_list):
        visits = super().create(vals_list)
        if any(self.EYE_TRIGGERS & set(vals) for vals in vals_list):
            visits._sync_eye_observations()
        return visits

    def write(self, vals):
        res = super().write(vals)
        if self.EYE_TRIGGERS & set(vals):
            self._sync_eye_observations()
        return res

    def action_eye_prescription(self):
        self.ensure_one()
        visit = self.sudo()
        rx = self.env['medical.eye.prescription'].create({
            'patient_id': self.patient_id.id, 'visit_id': self.id, 'doctor_id': self.doctor_id.id,
            'od_sph': visit.od_sph, 'od_cyl': visit.od_cyl, 'od_axis': visit.od_axis, 'od_add': visit.od_add,
            'os_sph': visit.os_sph, 'os_cyl': visit.os_cyl, 'os_axis': visit.os_axis, 'os_add': visit.os_add,
            'rx_type': 'progressive' if (visit.od_add or visit.os_add) else 'distance',
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.eye.prescription',
            'res_id': rx.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def _brief_specialty_facts(self):
        facts = super()._brief_specialty_facts()
        if self.is_eye:
            Observation = self.env['medical.observation'].sudo()
            rows = []
            for xmlid, label in (('medical_ophthalmology.obs_iop', _("IOP")), ('medical_ophthalmology.obs_va_logmar', _("VA logMAR"))):
                obs_type = self.env.ref(xmlid, raise_if_not_found=False)
                for side in ('right', 'left'):
                    last = Observation.search([('patient_id', '=', self.patient_id.id), ('type_id', '=', obs_type.id),
                                               ('laterality', '=', side), ('visit_id', '!=', self.id)], order='date desc', limit=1)
                    if last:
                        rows.append(f"{label} {'OD' if side == 'right' else 'OS'}: {last.value:g} ({last.date.date()})")
            facts.append((_("Previous eye measurements"), rows))
        return facts


class MedicalTreatmentLine(models.Model):
    _inherit = 'medical.treatment.line'

    def _site_parts(self):
        label, key = super()._site_parts()
        eye = self.env.ref('medical_ophthalmology.dept_ophthalmology', raise_if_not_found=False)
        if eye and self.department_id == eye and self.laterality and not self.body_site_id:
            label = {'right': _("Right eye (OD)"), 'left': _("Left eye (OS)"), 'bilateral': _("Both eyes (OU)")}[self.laterality]
            key = f"eye:{self.laterality}"
        return label, key


class MedicalEyePrescription(models.Model):
    _name = 'medical.eye.prescription'
    _description = 'Glasses Prescription'
    _order = 'date desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, domain=[('is_patient', '=', True)])
    visit_id = fields.Many2one('medical.visit', ondelete='set null')
    doctor_id = fields.Many2one('hr.employee', string='Doctor', domain=[('is_practitioner', '=', True)])
    date = fields.Date(default=fields.Date.context_today, required=True)
    rx_type = fields.Selection([('distance', 'Distance'), ('near', 'Reading'), ('bifocal', 'Bifocal'),
                                ('progressive', 'Progressive'), ('contact', 'Contact Lenses')],
                               string='Type', default='distance', required=True)
    od_sph = fields.Float(string='Right Sphere', digits=(4, 2))
    od_cyl = fields.Float(string='Right Cylinder', digits=(4, 2))
    od_axis = fields.Integer(string='Right Axis')
    od_add = fields.Float(string='Right Add', digits=(4, 2))
    os_sph = fields.Float(string='Left Sphere', digits=(4, 2))
    os_cyl = fields.Float(string='Left Cylinder', digits=(4, 2))
    os_axis = fields.Integer(string='Left Axis')
    os_add = fields.Float(string='Left Add', digits=(4, 2))
    pd = fields.Float(string='Pupillary Distance (mm)', digits=(4, 1))
    lens_notes = fields.Char(string='Lens Recommendations', help='Anti-reflective, photochromic, high index...')
    valid_months = fields.Integer(default=12)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.eye.prescription') or _('New')
        return super().create(vals_list)

    @api.constrains('od_axis', 'os_axis')
    def _check_axis(self):
        for rx in self:
            if any(axis and not 0 <= axis <= 180 for axis in (rx.od_axis, rx.os_axis)):
                raise ValidationError(_("The cylinder axis goes from 0 to 180 degrees."))

    @api.model
    def _fmt(self, value):
        return f"{value:+.2f}" if value else "0.00"

    def action_print(self):
        return self.env.ref('medical_ophthalmology.action_report_eye_prescription').report_action(self)


class MedicalSurgery(models.Model):
    _inherit = 'medical.surgery'

    is_eye = fields.Boolean(compute='_compute_is_eye')
    axial_length = fields.Float(string='Axial Length (mm)', digits=(5, 2))
    k1 = fields.Float(string='K1 (D)', digits=(5, 2))
    k2 = fields.Float(string='K2 (D)', digits=(5, 2))
    iol_model = fields.Char(string='IOL Model')
    iol_power = fields.Float(string='IOL Power (D)', digits=(4, 1))
    target_refraction = fields.Float(string='Target Refraction (D)', digits=(4, 2))

    @api.depends('department_id')
    def _compute_is_eye(self):
        eye = self.env.ref('medical_ophthalmology.dept_ophthalmology', raise_if_not_found=False)
        for surgery in self:
            surgery.is_eye = bool(eye) and surgery.department_id == eye
