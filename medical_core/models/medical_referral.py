from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MedicalReferral(models.Model):
    _name = 'medical.referral'
    _description = 'Referral'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, domain=[('is_patient', '=', True)])
    visit_id = fields.Many2one('medical.visit', string='From Visit', index='btree_not_null', ondelete='set null')
    date = fields.Date(default=fields.Date.context_today, required=True)
    from_doctor_id = fields.Many2one('hr.employee', string='Referred by', required=True,
                                     domain=[('is_practitioner', '=', True)])
    from_department_id = fields.Many2one('medical.department', string='From Clinic')
    referral_type = fields.Selection([('internal', 'To another clinic here'), ('external', 'Outside the center')],
                                     default='internal', required=True)
    to_department_id = fields.Many2one('medical.department', string='To Clinic')
    to_doctor_id = fields.Many2one('hr.employee', string='To Doctor', domain=[('is_practitioner', '=', True)])
    external_partner_id = fields.Many2one('res.partner', string='External Doctor / Hospital')
    urgency = fields.Selection([('routine', 'Routine'), ('soon', 'Within a week'), ('urgent', 'Urgent')],
                               default='routine', required=True, tracking=True)
    reason = fields.Text(required=True)
    clinical_summary = fields.Html(groups='medical_core.group_medical_doctor')
    state = fields.Selection([
        ('sent', 'Sent'),
        ('booked', 'Booked'),
        ('done', 'Seen'),
        ('cancelled', 'Cancelled'),
    ], default='sent', required=True, tracking=True, copy=False)
    booked_visit_id = fields.Many2one('medical.visit', string='Booked Visit', copy=False, readonly=True)
    reply = fields.Text(string='Reply from the Specialist')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.referral') or _('New')
        referrals = super().create(vals_list)
        for referral in referrals.filtered(lambda r: r.referral_type == 'internal'):
            receiver = referral.to_doctor_id.user_id or referral.to_department_id.manager_id.user_id
            if receiver:
                referral.activity_schedule('mail.mail_activity_data_todo', user_id=receiver.id,
                                           summary=_("New referral: %s", referral.patient_id.name))
        return referrals

    def action_book(self):
        self.ensure_one()
        if self.referral_type != 'internal':
            raise UserError(_("External referrals are not booked here."))
        doctor = self.to_doctor_id or self.to_department_id.practitioner_ids[:1]
        if not doctor:
            raise UserError(_("Choose the doctor who will see the patient."))
        visit = self.env['medical.visit'].create({
            'patient_id': self.patient_id.id,
            'doctor_id': doctor.id,
            'department_id': self.to_department_id.id,
            'visit_type': 'new',
        })
        self.write({'booked_visit_id': visit.id, 'state': 'booked'})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.visit',
            'res_id': visit.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_done(self):
        self.write({'state': 'done'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_print(self):
        return self.env.ref('medical_core.action_report_medical_referral').report_action(self)
