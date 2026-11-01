from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _

DOCTOR = 'medical_core.group_medical_doctor'
NURSE = 'medical_core.group_medical_nurse'

BLOOD_GROUPS = [(g, g) for g in ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_patient = fields.Boolean(string='Is a Patient', index=True)
    patient_code = fields.Char(string='Patient Code', copy=False, readonly=True, index='btree_not_null')
    birthdate = fields.Date(string='Date of Birth')
    patient_age = fields.Integer(string='Age', compute='_compute_patient_age')
    patient_age_months = fields.Integer(string='Age (months)', compute='_compute_patient_age')
    patient_age_label = fields.Char(string='Age ', compute='_compute_patient_age')
    gender = fields.Selection([('male', 'Male'), ('female', 'Female')])
    national_id = fields.Char(string='National ID')
    blood_group = fields.Selection(BLOOD_GROUPS, groups=NURSE)
    emergency_contact = fields.Char(string='Emergency Contact')
    emergency_phone = fields.Char(string='Emergency Phone')
    occupation = fields.Char()
    medical_alerts = fields.Text(string='Medical Alerts',
                                 help='Anticoagulants, pacemaker, pregnancy... shown on every visit.',
                                 groups=NURSE)
    allergen_ids = fields.Many2many('medical.allergen', string='Allergies', groups=NURSE)
    medical_history = fields.Html(string='Medical History', groups=DOCTOR)
    has_medical_alert = fields.Boolean(compute='_compute_has_medical_alert')
    problem_ids = fields.One2many('medical.patient.problem', 'patient_id', string='Problem List', groups=DOCTOR)
    medical_visit_ids = fields.One2many('medical.visit', 'patient_id', string='Visits')
    medical_plan_ids = fields.One2many('medical.treatment.plan', 'patient_id', string='Treatment Plans')
    observation_ids = fields.One2many('medical.observation', 'patient_id', string='Measurements', groups=NURSE)
    medical_visit_count = fields.Integer(compute='_compute_medical_counts')
    medical_plan_count = fields.Integer(compute='_compute_medical_counts')
    medical_prescription_count = fields.Integer(compute='_compute_medical_counts')
    medical_order_count = fields.Integer(compute='_compute_medical_counts')
    medical_contract_count = fields.Integer(compute='_compute_medical_counts')
    last_visit_date = fields.Datetime(compute='_compute_medical_counts')

    _patient_code_uniq = models.Constraint('UNIQUE(patient_code)', 'Patient code must be unique.')

    @api.depends('birthdate')
    def _compute_patient_age(self):
        today = fields.Date.context_today(self)
        for partner in self:
            if not partner.birthdate:
                partner.patient_age = partner.patient_age_months = 0
                partner.patient_age_label = False
                continue
            delta = relativedelta(today, partner.birthdate)
            partner.patient_age = delta.years
            partner.patient_age_months = delta.years * 12 + delta.months
            if delta.years >= 2:
                partner.patient_age_label = _("%s years", delta.years)
            elif partner.patient_age_months >= 1:
                partner.patient_age_label = _("%s months", partner.patient_age_months)
            else:
                partner.patient_age_label = _("%s days", (today - partner.birthdate).days)

    def _compute_has_medical_alert(self):
        # sudo: the flag (not the content) is visible to reception so they can warn the doctor
        for partner in self:
            sudo = partner.sudo()
            partner.has_medical_alert = bool(sudo.medical_alerts or sudo.allergen_ids)

    def _compute_medical_counts(self):
        def count(model, field='patient_id'):
            if model not in self.env:
                return {}
            return dict(self.env[model]._read_group([(field, 'in', self.ids)], [field], ['__count']))
        visits, plans = count('medical.visit'), count('medical.treatment.plan')
        prescriptions, orders = count('medical.prescription'), count('medical.order')
        contracts = count('medical.care.contract')
        last = dict(self.env['medical.visit']._read_group(
            [('patient_id', 'in', self.ids), ('state', 'in', ('done', 'invoiced'))], ['patient_id'], ['start:max']))
        for partner in self:
            partner.medical_visit_count = visits.get(partner, 0)
            partner.medical_plan_count = plans.get(partner, 0)
            partner.medical_prescription_count = prescriptions.get(partner, 0)
            partner.medical_order_count = orders.get(partner, 0)
            partner.medical_contract_count = contracts.get(partner, 0)
            partner.last_visit_date = last.get(partner)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('is_patient') and not vals.get('patient_code'):
                vals['patient_code'] = self.env['ir.sequence'].next_by_code('medical.patient')
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if vals.get('is_patient'):
            for partner in self.filtered(lambda p: not p.patient_code):
                partner.patient_code = self.env['ir.sequence'].next_by_code('medical.patient')
        return res

    @api.depends('patient_code')
    def _compute_display_name(self):
        super()._compute_display_name()
        for partner in self:
            if partner.is_patient and partner.patient_code:
                partner.display_name = f"[{partner.patient_code}] {partner.display_name}"

    @api.model
    def _search_display_name(self, operator, value):
        domain = super()._search_display_name(operator, value)
        if operator in ('ilike', '=ilike', 'like', '=like') and value:
            domain = ['|', ('patient_code', operator, value)] + list(domain)
        return domain

    # ------------------------------------------------------------------ helpers
    def _medical_allergy_conflicts(self, products):
        """Allergens of `products` the patient is allergic to, as {product: allergens}."""
        self.ensure_one()
        allergies = self.sudo().allergen_ids
        return {p: p.allergen_ids & allergies for p in products if p.allergen_ids & allergies}

    def _open_patient_records(self, xmlid, extra_context=None):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(xmlid)
        action['domain'] = [('patient_id', '=', self.id)]
        action['context'] = dict({'default_patient_id': self.id}, **(extra_context or {}))
        return action

    def action_open_medical_visits(self):
        return self._open_patient_records('medical_core.action_medical_visit_history')

    def action_open_medical_plans(self):
        return self._open_patient_records('medical_core.action_medical_treatment_plan')

    def action_open_medical_prescriptions(self):
        return self._open_patient_records('medical_core.action_medical_prescription')

    def action_open_medical_orders(self):
        return self._open_patient_records('medical_core.action_medical_order')

    def action_open_medical_contracts(self):
        return self._open_patient_records('medical_core.action_medical_care_contract')

    def action_book_visit(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.visit',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'context': {'default_patient_id': self.id},
        }
