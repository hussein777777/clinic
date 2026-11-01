from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

ACTIVE_STATES = ('draft', 'confirmed', 'checked_in', 'in_consult')
DOCTOR = 'medical_core.group_medical_doctor'
NURSE = 'medical_core.group_medical_nurse'

# vital field -> observation type xmlid
VITAL_FIELDS = {
    'bp_systolic': 'medical_core.obs_bp_systolic',
    'bp_diastolic': 'medical_core.obs_bp_diastolic',
    'pulse': 'medical_core.obs_pulse',
    'temperature': 'medical_core.obs_temperature',
    'resp_rate': 'medical_core.obs_resp_rate',
    'spo2': 'medical_core.obs_spo2',
    'weight': 'medical_core.obs_weight',
    'height': 'medical_core.obs_height',
    'bmi': 'medical_core.obs_bmi',
    'pain_score': 'medical_core.obs_pain',
}


class MedicalVisit(models.Model):
    _name = 'medical.visit'
    _description = 'Visit'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, tracking=True,
                                 domain=[('is_patient', '=', True)])
    patient_phone = fields.Char(related='patient_id.phone')
    patient_age_label = fields.Char(related='patient_id.patient_age_label', string='Age')
    patient_gender = fields.Selection(related='patient_id.gender', string='Gender')
    has_medical_alert = fields.Boolean(related='patient_id.has_medical_alert')
    doctor_id = fields.Many2one('hr.employee', string='Doctor', required=True, tracking=True,
                                domain=[('is_practitioner', '=', True)])
    department_id = fields.Many2one('medical.department', string='Department', compute='_compute_department_id',
                                    store=True, readonly=False, precompute=True, index=True, tracking=True)
    room_id = fields.Many2one('medical.room', string='Room', tracking=True)
    visit_type = fields.Selection([
        ('new', 'New Patient'),
        ('follow_up', 'Follow-up'),
        ('procedure', 'Procedure'),
        ('session', 'Therapy Session'),
        ('emergency', 'Emergency'),
    ], default='new', required=True)
    start = fields.Datetime(required=True, default=fields.Datetime.now, tracking=True)
    duration = fields.Float(string='Duration (h)', default=0.5)
    stop = fields.Datetime(compute='_compute_stop', store=True)
    state = fields.Selection([
        ('draft', 'Booked'),
        ('confirmed', 'Confirmed'),
        ('checked_in', 'Checked In'),
        ('in_consult', 'In Consultation'),
        ('done', 'Done'),
        ('invoiced', 'Invoiced'),
        ('cancelled', 'Cancelled'),
        ('no_show', 'No Show'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    line_ids = fields.One2many('medical.treatment.line', 'visit_id', string='Treatments')
    plan_id = fields.Many2one('medical.treatment.plan', string='Treatment Plan',
                              domain="[('patient_id', '=', patient_id), ('state', 'in', ('accepted', 'in_progress'))]")
    care_contract_id = fields.Many2one('medical.care.contract', string='Care Program', index='btree_not_null',
                                       domain="[('patient_id', '=', patient_id), ('state', '=', 'active')]")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    amount_total = fields.Monetary(compute='_compute_amount_total', store=True)
    color = fields.Integer(related='department_id.color')

    # ------------------------------------------------------------- clinical record
    chief_complaint = fields.Text(string='Chief Complaint', groups=NURSE)
    history = fields.Html(string='History of Present Illness', groups=DOCTOR)
    examination = fields.Html(string='Examination', groups=DOCTOR)
    note = fields.Html(string='Assessment & Plan', groups=DOCTOR)
    diagnosis_ids = fields.One2many('medical.visit.diagnosis', 'visit_id', string='Diagnoses', groups=DOCTOR)
    primary_diagnosis_id = fields.Many2one('medical.diagnosis', compute='_compute_primary_diagnosis', store=True,
                                           string='Main Diagnosis', groups=DOCTOR)
    prescription_ids = fields.One2many('medical.prescription', 'visit_id', string='Prescriptions')
    order_ids = fields.One2many('medical.order', 'visit_id', string='Orders')
    assessment_ids = fields.One2many('medical.assessment', 'visit_id', string='Scores', groups=NURSE)
    referral_ids = fields.One2many('medical.referral', 'visit_id', string='Referrals')
    exam_observation_ids = fields.One2many('medical.observation', 'visit_id', string='Examination Measurements',
                                           domain=[('source', '=', 'exam')], groups=NURSE)
    prescription_count = fields.Integer(compute='_compute_counts')
    order_count = fields.Integer(compute='_compute_counts')

    # ---------------------------------------------------------------------- vitals
    bp_systolic = fields.Integer(string='BP Systolic', groups=NURSE)
    bp_diastolic = fields.Integer(string='BP Diastolic', groups=NURSE)
    pulse = fields.Integer(string='Pulse (bpm)', groups=NURSE)
    temperature = fields.Float(string='Temperature (°C)', digits=(4, 1), groups=NURSE)
    resp_rate = fields.Integer(string='Respiratory Rate', groups=NURSE)
    spo2 = fields.Integer(string='SpO₂ (%)', groups=NURSE)
    weight = fields.Float(string='Weight (kg)', digits=(6, 2), groups=NURSE)
    height = fields.Float(string='Height (cm)', digits=(5, 1), groups=NURSE)
    bmi = fields.Float(string='BMI', digits=(4, 1), compute='_compute_bmi', store=True, groups=NURSE)
    pain_score = fields.Integer(string='Pain (0-10)', groups=NURSE)
    vitals_warning = fields.Char(compute='_compute_vitals_warning', groups=NURSE)

    # ------------------------------------------------------------------- follow-up
    follow_up_in = fields.Integer(string='Follow-up in')
    follow_up_unit = fields.Selection([('days', 'Days'), ('weeks', 'Weeks'), ('months', 'Months')], default='weeks')
    follow_up_date = fields.Date(compute='_compute_follow_up_date', store=True, readonly=False)
    follow_up_visit_id = fields.Many2one('medical.visit', string='Follow-up Booking', copy=False, readonly=True)

    # -------------------------------------------------------------------- computes
    @api.depends('doctor_id', 'room_id')
    def _compute_department_id(self):
        for visit in self:
            if visit.department_id:
                continue
            departments = visit.doctor_id.sudo().medical_department_ids
            visit.department_id = visit.room_id.department_id if visit.room_id.department_id in departments \
                else departments[:1]

    @api.depends('start', 'duration')
    def _compute_stop(self):
        for visit in self:
            visit.stop = visit.start + timedelta(hours=visit.duration or 0.5) if visit.start else False

    @api.depends('line_ids.price_subtotal', 'line_ids.state')
    def _compute_amount_total(self):
        for visit in self:
            visit.amount_total = sum(visit.line_ids.filtered(lambda l: l.state != 'cancelled').mapped('price_subtotal'))

    @api.depends('diagnosis_ids.is_primary', 'diagnosis_ids.diagnosis_id')
    def _compute_primary_diagnosis(self):
        for visit in self:
            primary = visit.diagnosis_ids.filtered('is_primary')[:1] or visit.diagnosis_ids[:1]
            visit.primary_diagnosis_id = primary.diagnosis_id

    def _compute_counts(self):
        for visit in self:
            visit.prescription_count = len(visit.prescription_ids)
            visit.order_count = len(visit.order_ids)

    @api.depends('weight', 'height')
    def _compute_bmi(self):
        for visit in self:
            visit.bmi = visit.weight / (visit.height / 100.0) ** 2 if visit.weight and visit.height else 0.0

    @api.depends('bp_systolic', 'bp_diastolic', 'pulse', 'temperature', 'spo2', 'resp_rate')
    def _compute_vitals_warning(self):
        for visit in self:
            visit.vitals_warning = visit._vitals_alerts() or False

    def _vitals_alerts(self):
        """Out-of-range vitals as a short human sentence, using the observation type limits."""
        self.ensure_one()
        alerts = []
        for field, xmlid in VITAL_FIELDS.items():
            value = self[field]
            if not value or field in ('weight', 'height'):
                continue
            obs_type = self.env.ref(xmlid, raise_if_not_found=False)
            flag = obs_type._flag(value) if obs_type else False
            if flag in ('low', 'high', 'critical_low', 'critical_high'):
                label = self._fields[field].get_description(self.env)['string']
                alerts.append(f"{label} {value:g}")
        return ' · '.join(alerts)

    @api.depends('start', 'follow_up_in', 'follow_up_unit')
    def _compute_follow_up_date(self):
        for visit in self:
            if visit.follow_up_in and visit.start:
                visit.follow_up_date = visit.start.date() + relativedelta(**{visit.follow_up_unit or 'weeks': visit.follow_up_in})
            elif not visit.follow_up_in:
                visit.follow_up_date = visit.follow_up_date or False

    # ------------------------------------------------------------------ constraints
    @api.constrains('room_id', 'start', 'stop', 'state')
    def _check_room_overlap(self):
        for visit in self.filtered(lambda v: v.room_id and v.state in ACTIVE_STATES):
            overlap = self.search_count([
                ('id', '!=', visit.id),
                ('room_id', '=', visit.room_id.id),
                ('state', 'in', ACTIVE_STATES),
                ('start', '<', visit.stop),
                ('stop', '>', visit.start),
            ], limit=1)
            if overlap:
                raise ValidationError(_("Room %(room)s is already booked at that time.", room=visit.room_id.name))

    @api.constrains('pain_score', 'spo2')
    def _check_vitals(self):
        for visit in self.sudo():
            if visit.pain_score and not 0 <= visit.pain_score <= 10:
                raise ValidationError(_("The pain score goes from 0 to 10."))
            if visit.spo2 and not 0 < visit.spo2 <= 100:
                raise ValidationError(_("SpO₂ is a percentage between 1 and 100."))

    @api.onchange('doctor_id')
    def _onchange_doctor_id(self):
        if self.doctor_id and not self.room_id:
            self.room_id = self.doctor_id.default_room_id

    @api.onchange('department_id')
    def _onchange_department_id(self):
        if self.department_id and self.department_id.default_visit_duration and not self._origin.id:
            self.duration = self.department_id.default_visit_duration

    # ------------------------------------------------------------------------- CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.visit') or _('New')
        visits = super().create(vals_list)
        if any(f in vals for vals in vals_list for f in VITAL_FIELDS):
            visits._sync_vital_observations()
        return visits

    def write(self, vals):
        res = super().write(vals)
        if set(VITAL_FIELDS) & set(vals) or 'start' in vals:
            self._sync_vital_observations()
        return res

    def _sync_vital_observations(self):
        """Every vital typed on the visit is also a dated measurement on the patient (for trends)."""
        Observation = self.env['medical.observation'].sudo()
        for visit in self.sudo():
            existing = Observation.search([('visit_id', '=', visit.id), ('source', '=', 'vitals')])
            by_type = {o.type_id.id: o for o in existing}
            for field, xmlid in VITAL_FIELDS.items():
                obs_type = self.env.ref(xmlid, raise_if_not_found=False)
                if not obs_type:
                    continue
                value = visit[field]
                current = by_type.get(obs_type.id)
                if value:
                    vals = {'value': value, 'date': visit.start}
                    if current:
                        current.write(vals)
                    else:
                        Observation.create(dict(vals, patient_id=visit.patient_id.id, type_id=obs_type.id,
                                                visit_id=visit.id, source='vitals'))
                elif current:
                    current.unlink()

    # --------------------------------------------------------------------- workflow
    def action_confirm(self):
        self.filtered(lambda v: v.state == 'draft').write({'state': 'confirmed'})

    def action_check_in(self):
        self.filtered(lambda v: v.state in ('draft', 'confirmed')).write({'state': 'checked_in'})

    def action_start(self):
        self.filtered(lambda v: v.state in ('draft', 'confirmed', 'checked_in')).write({'state': 'in_consult'})

    def _check_can_close(self):
        for visit in self:
            if visit.state not in ACTIVE_STATES:
                raise UserError(_("Visit %s is not open.", visit.name))
            if not visit.line_ids.filtered(lambda l: l.state != 'cancelled'):
                raise UserError(_("Add at least one treatment to visit %s before closing it.", visit.name))

    def action_done(self):
        """Execute every remaining scheduled line and close the visit clinically.

        medical_insurance creates the claim lines and medical_account the patient invoice
        by extending this method.
        """
        self._check_can_close()
        self.line_ids.filtered(lambda l: l.state in ('planned', 'scheduled')).action_execute()
        self.write({'state': 'done'})
        self._update_problem_list()
        return True

    def _update_problem_list(self):
        """Chronic diagnoses are added to the patient problem list once."""
        Problem = self.env['medical.patient.problem'].sudo()
        for visit in self.sudo():
            known = visit.patient_id.problem_ids.diagnosis_id
            for line in visit.diagnosis_ids.filtered(
                    lambda d: d.diagnosis_id.is_chronic and d.certainty == 'confirmed' and d.diagnosis_id not in known):
                Problem.create({
                    'patient_id': visit.patient_id.id,
                    'diagnosis_id': line.diagnosis_id.id,
                    'onset_date': visit.start.date(),
                    'department_id': visit.department_id.id,
                })
                known |= line.diagnosis_id

    def action_cancel(self):
        if any(v.state in ('done', 'invoiced') for v in self):
            raise UserError(_("Closed visits cannot be cancelled here."))
        self.line_ids.filtered(lambda l: l.state == 'scheduled').write({'visit_id': False})
        self.write({'state': 'cancelled'})

    def action_no_show(self):
        self.filtered(lambda v: v.state in ('draft', 'confirmed')).write({'state': 'no_show'})
        self.line_ids.filtered(lambda l: l.state == 'scheduled').write({'visit_id': False})

    def action_reset_draft(self):
        self.filtered(lambda v: v.state in ('cancelled', 'no_show')).write({'state': 'draft'})

    def action_add_plan_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Add Planned Treatments'),
            'res_model': 'medical.visit.add.lines',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_visit_id': self.id},
        }

    def _follow_up_start(self):
        """Same local time of day as this visit, on the follow-up date."""
        self.ensure_one()
        tz_start = fields.Datetime.context_timestamp(self, self.start)
        local = datetime.combine(self.follow_up_date, tz_start.time() if tz_start else time(10, 0))
        offset = tz_start.utcoffset() if tz_start else timedelta(0)
        return local - offset

    def _prepare_follow_up_vals(self):
        self.ensure_one()
        follow_up = self.env.ref('medical_core.product_follow_up', raise_if_not_found=False)
        start = self._follow_up_start()
        room = self.room_id
        if room and self.search_count([('room_id', '=', room.id), ('state', 'in', ACTIVE_STATES),
                                       ('start', '<', start + timedelta(hours=self.duration or 0.5)),
                                       ('stop', '>', start)], limit=1):
            room = room.browse()  # taken: reception picks another one
        return {
            'patient_id': self.patient_id.id,
            'doctor_id': self.doctor_id.id,
            'department_id': self.department_id.id,
            'room_id': room.id,
            'visit_type': 'follow_up',
            'start': start,
            'duration': self.duration,
            'plan_id': self.plan_id.id,
            'care_contract_id': self.care_contract_id.id,
            'line_ids': [(0, 0, {'product_id': follow_up.id})] if follow_up else [],
        }

    def action_book_follow_up(self):
        self.ensure_one()
        if not self.follow_up_date:
            raise UserError(_("Set when the patient should come back first."))
        if not self.follow_up_visit_id:
            visit = self.create(self._prepare_follow_up_vals())
            self.follow_up_visit_id = visit
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.visit',
            'res_id': self.follow_up_visit_id.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def _open_related(self, model, field='visit_id', extra=None):
        self.ensure_one()
        ctx = {'default_visit_id': self.id, 'default_patient_id': self.patient_id.id,
               'default_doctor_id': self.doctor_id.id}
        ctx.update(extra or {})
        return {
            'type': 'ir.actions.act_window',
            'res_model': model,
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [(field, '=', self.id)],
            'context': ctx,
        }

    def action_new_prescription(self):
        self.ensure_one()
        prescription = self.prescription_ids.filtered(lambda p: p.state == 'draft')[:1] or \
            self.env['medical.prescription'].create({
                'visit_id': self.id, 'patient_id': self.patient_id.id, 'doctor_id': self.doctor_id.id})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.prescription',
            'res_id': prescription.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_view_prescriptions(self):
        return self._open_related('medical.prescription')

    def action_new_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.order',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'context': {'default_visit_id': self.id, 'default_patient_id': self.patient_id.id,
                        'default_doctor_id': self.doctor_id.id},
        }

    def action_view_orders(self):
        return self._open_related('medical.order')

    def action_refer(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.referral',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
            'context': {'default_visit_id': self.id, 'default_patient_id': self.patient_id.id,
                        'default_from_doctor_id': self.doctor_id.id,
                        'default_from_department_id': self.department_id.id},
        }

    def action_new_assessment(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.assessment',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
            'context': {'default_visit_id': self.id, 'default_patient_id': self.patient_id.id,
                        'default_department_id': self.department_id.id},
        }

    def _brief_specialty_facts(self):
        """Specialty findings for summaries and the AI brief, as [(title, [rows])] (tooth chart, refraction...)."""
        self.ensure_one()
        return []

    def action_print_report(self):
        return self.env.ref('medical_core.action_report_medical_visit').report_action(self)
