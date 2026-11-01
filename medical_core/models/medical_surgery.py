from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .medical_body_site import LATERALITY

SIGN_IN = ('check_identity', 'check_site_marked', 'check_consent', 'check_anesthesia', 'check_allergy', 'check_fasting')
TIME_OUT = ('check_team', 'check_procedure_confirmed', 'check_antibiotic', 'check_imaging')
SIGN_OUT = ('check_counts', 'check_specimens', 'check_equipment')


class MedicalSurgery(models.Model):
    """Operation booked in a theatre, with the WHO surgical safety checklist and the operative note.

    The billing side is an ordinary procedure visit (`visit_id`): the operation's procedures, implants and
    consumables are its treatment lines, so insurance, invoicing and stock work as for any visit.
    """
    _name = 'medical.surgery'
    _description = 'Surgery'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'scheduled_start desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, tracking=True,
                                 domain=[('is_patient', '=', True)])
    surgeon_id = fields.Many2one('hr.employee', string='Surgeon', required=True, tracking=True,
                                 domain=[('is_practitioner', '=', True)])
    assistant_ids = fields.Many2many('hr.employee', 'medical_surgery_assistant_rel', 'surgery_id', 'employee_id',
                                     string='Assistants')
    anesthetist_id = fields.Many2one('hr.employee', string='Anesthetist')
    department_id = fields.Many2one('medical.department', compute='_compute_department_id', store=True,
                                    readonly=False, precompute=True, index=True)
    room_id = fields.Many2one('medical.room', string='Theatre', domain=[('room_type', '=', 'theatre')])
    scheduled_start = fields.Datetime(required=True, tracking=True)
    duration = fields.Float(string='Duration (h)', default=1.0)
    priority = fields.Selection([('elective', 'Elective'), ('urgent', 'Urgent'), ('emergency', 'Emergency')],
                                default='elective', required=True, tracking=True)
    anesthesia_type = fields.Selection([
        ('general', 'General'),
        ('spinal', 'Spinal'),
        ('epidural', 'Epidural'),
        ('regional', 'Regional Block'),
        ('local', 'Local'),
        ('sedation', 'Sedation'),
    ], default='general', required=True)
    body_site_id = fields.Many2one('medical.body.site', string='Operative Site')
    laterality = fields.Selection(LATERALITY, string='Side')
    visit_id = fields.Many2one('medical.visit', string='Billing Visit', readonly=True, copy=False, index=True)
    line_ids = fields.One2many(related='visit_id.line_ids', readonly=False, string='Procedures & Materials')
    preop_visit_id = fields.Many2one('medical.visit', string='Pre-op Consultation')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('in_theatre', 'In Theatre'),
        ('recovery', 'Recovery'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)

    # WHO surgical safety checklist
    check_identity = fields.Boolean(string='Identity and procedure confirmed with the patient')
    check_site_marked = fields.Boolean(string='Site marked')
    check_consent = fields.Boolean(string='Consent signed', compute='_compute_check_consent', store=True,
                                   readonly=False)
    check_anesthesia = fields.Boolean(string='Anesthesia machine and medication checked')
    check_allergy = fields.Boolean(string='Known allergies reviewed')
    check_fasting = fields.Boolean(string='Fasting confirmed')
    check_team = fields.Boolean(string='Team members introduced')
    check_procedure_confirmed = fields.Boolean(string='Patient, site and procedure confirmed aloud')
    check_antibiotic = fields.Boolean(string='Antibiotic prophylaxis given (or not needed)')
    check_imaging = fields.Boolean(string='Essential imaging displayed')
    check_counts = fields.Boolean(string='Instrument, sponge and needle counts correct')
    check_specimens = fields.Boolean(string='Specimens labelled')
    check_equipment = fields.Boolean(string='Equipment problems addressed')
    sign_in_done = fields.Boolean(compute='_compute_checklist')
    time_out_done = fields.Boolean(compute='_compute_checklist')
    sign_out_done = fields.Boolean(compute='_compute_checklist')

    # operative record
    actual_start = fields.Datetime(readonly=True, copy=False)
    actual_end = fields.Datetime(readonly=True, copy=False)
    findings = fields.Html(groups='medical_core.group_medical_doctor')
    operative_note = fields.Html(string='Operative Note', groups='medical_core.group_medical_doctor')
    complications = fields.Text(groups='medical_core.group_medical_doctor')
    blood_loss_ml = fields.Integer(string='Blood Loss (ml)')
    post_op_instructions = fields.Html(string='Post-operative Orders')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    @api.depends('surgeon_id')
    def _compute_department_id(self):
        for surgery in self:
            if not surgery.department_id:
                surgery.department_id = surgery.surgeon_id.sudo().medical_department_ids[:1]

    @api.depends('visit_id.line_ids.consent_id.signed', 'visit_id.line_ids.product_id.requires_consent')
    def _compute_check_consent(self):
        for surgery in self:
            needing = surgery.visit_id.line_ids.filtered(lambda l: l.product_id.requires_consent)
            if needing:
                surgery.check_consent = all(needing.consent_id.mapped('signed')) and len(needing.consent_id) == len(needing)

    @api.depends(*SIGN_IN, *TIME_OUT, *SIGN_OUT)
    def _compute_checklist(self):
        for surgery in self:
            surgery.sign_in_done = all(surgery[f] for f in SIGN_IN)
            surgery.time_out_done = all(surgery[f] for f in TIME_OUT)
            surgery.sign_out_done = all(surgery[f] for f in SIGN_OUT)

    @api.constrains('duration')
    def _check_duration(self):
        if any(s.duration <= 0 for s in self):
            raise ValidationError(_("The duration must be positive."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.surgery') or _('New')
        surgeries = super().create(vals_list)
        for surgery in surgeries:
            surgery.visit_id = self.env['medical.visit'].create(surgery._prepare_visit_vals())
        return surgeries

    def _prepare_visit_vals(self):
        self.ensure_one()
        return {
            'patient_id': self.patient_id.id,
            'doctor_id': self.surgeon_id.id,
            'department_id': self.department_id.id,
            'room_id': self.room_id.id,
            'start': self.scheduled_start,
            'duration': self.duration,
            'visit_type': 'procedure',
            'company_id': self.company_id.id,
        }

    def write(self, vals):
        res = super().write(vals)
        sync = {k: vals[k] for k in ('scheduled_start', 'duration', 'room_id', 'surgeon_id') if k in vals}
        if sync:
            mapping = {'scheduled_start': 'start', 'duration': 'duration', 'room_id': 'room_id', 'surgeon_id': 'doctor_id'}
            self.visit_id.filtered(lambda v: v.state in ('draft', 'confirmed')).write(
                {mapping[k]: v for k, v in sync.items()})
        return res

    def _compute_display_name(self):
        for surgery in self:
            procedures = ', '.join(surgery.visit_id.line_ids.filtered(
                lambda l: l.procedure_type == 'surgery').product_id.mapped('name'))
            surgery.display_name = f"{surgery.name} - {procedures}" if procedures else surgery.name

    # ---------------------------------------------------------------- workflow
    def action_schedule(self):
        for surgery in self:
            if not surgery.visit_id.line_ids:
                raise UserError(_("Add the planned procedure first."))
            if not surgery.room_id:
                raise UserError(_("Choose the theatre."))
        self.write({'state': 'scheduled'})
        self.visit_id.action_confirm()

    def action_enter_theatre(self):
        for surgery in self:
            if not surgery.sign_in_done:
                raise UserError(_("Complete the sign-in part of the safety checklist before the patient enters."))
        self.write({'state': 'in_theatre', 'actual_start': fields.Datetime.now()})
        self.visit_id.action_start()

    def action_to_recovery(self):
        for surgery in self:
            if not surgery.time_out_done:
                raise UserError(_("The time-out was not completed."))
            if not surgery.sign_out_done:
                raise UserError(_("Complete the sign-out before the patient leaves the theatre."))
        self.write({'state': 'recovery', 'actual_end': fields.Datetime.now()})

    def action_done(self):
        for surgery in self:
            if surgery.state != 'recovery':
                raise UserError(_("The patient must go through recovery first."))
            if not surgery.sudo().operative_note:
                raise UserError(_("Write the operative note before closing %s.", surgery.name))
        self.visit_id.filtered(lambda v: v.state not in ('done', 'invoiced')).action_done()
        self.write({'state': 'done'})

    def action_cancel(self):
        if any(s.state in ('in_theatre', 'recovery', 'done') for s in self):
            raise UserError(_("An operation in progress cannot be cancelled."))
        self.visit_id.filtered(lambda v: v.state not in ('done', 'invoiced', 'cancelled')).action_cancel()
        self.write({'state': 'cancelled'})

    def action_open_visit(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.visit',
            'res_id': self.visit_id.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_print(self):
        return self.env.ref('medical_core.action_report_medical_surgery').report_action(self)
