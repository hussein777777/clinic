from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

CAST_TYPES = [
    ('plaster', 'Plaster Cast'),
    ('fiberglass', 'Fiberglass Cast'),
    ('backslab', 'Backslab / Splint'),
    ('brace', 'Brace / Orthosis'),
    ('boot', 'Walking Boot'),
    ('sling', 'Sling'),
    ('collar', 'Cervical Collar'),
    ('bandage', 'Compression Bandage'),
]


class MedicalOrthopedicCast(models.Model):
    """A cast, splint or brace put on a patient: when, by whom, and when it must come off."""
    _name = 'medical.orthopedic.cast'
    _description = 'Cast / Splint'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'applied_date desc, id desc'

    case_id = fields.Many2one('medical.orthopedic.case', string='Orthopedic Case', index=True, ondelete='cascade')
    patient_id = fields.Many2one('res.partner', required=True, index=True, domain=[('is_patient', '=', True)])
    doctor_id = fields.Many2one('hr.employee', string='Applied by', domain=[('is_practitioner', '=', True)])
    visit_id = fields.Many2one('medical.visit', ondelete='set null')
    cast_type = fields.Selection(CAST_TYPES, required=True, default='plaster')
    body_site_id = fields.Many2one('medical.body.site', string='Site', required=True)
    laterality = fields.Selection([('right', 'Right'), ('left', 'Left')], string='Side')
    position = fields.Char(help='e.g. wrist in neutral, ankle at 90°')
    applied_date = fields.Date(default=fields.Date.context_today, required=True)
    removal_due = fields.Date(string='Remove on', required=True, tracking=True)
    removed_date = fields.Date(readonly=True, copy=False, tracking=True)
    state = fields.Selection([('on', 'On'), ('removed', 'Removed'), ('replaced', 'Replaced')],
                             default='on', required=True, tracking=True, index=True)
    is_overdue = fields.Boolean(compute='_compute_is_overdue', search='_search_is_overdue')
    days_on = fields.Integer(compute='_compute_days_on')
    skin_condition = fields.Selection([('ok', 'Intact'), ('redness', 'Redness'), ('sore', 'Pressure Sore'),
                                       ('swelling', 'Swelling')], string='Skin at Removal')
    note = fields.Char()
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    @api.depends('removal_due', 'state')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for cast in self:
            cast.is_overdue = cast.state == 'on' and cast.removal_due and cast.removal_due < today

    def _search_is_overdue(self, operator, value):
        if operator in ('in', 'not in'):
            value = True in value
            operator = '=' if operator == 'in' else '!='
        domain = [('state', '=', 'on'), ('removal_due', '<', fields.Date.context_today(self))]
        return domain if (operator == '=') == bool(value) else ['!', '&', *domain]

    @api.depends('applied_date', 'removed_date')
    def _compute_days_on(self):
        today = fields.Date.context_today(self)
        for cast in self:
            cast.days_on = ((cast.removed_date or today) - cast.applied_date).days if cast.applied_date else 0

    @api.constrains('applied_date', 'removal_due')
    def _check_dates(self):
        for cast in self:
            if cast.removal_due < cast.applied_date:
                raise UserError(_("The removal date must be after the application date."))

    @api.depends('cast_type', 'body_site_id', 'laterality')
    def _compute_display_name(self):
        types = dict(self._fields['cast_type']._description_selection(self.env))
        sides = dict(self._fields['laterality']._description_selection(self.env))
        for cast in self:
            parts = [types.get(cast.cast_type), cast.body_site_id.name, sides.get(cast.laterality)]
            cast.display_name = ' '.join(p for p in parts if p)

    @api.model_create_multi
    def create(self, vals_list):
        casts = super().create(vals_list)
        for cast in casts:
            cast._schedule_removal_reminder()
            if cast.case_id and cast.case_id.state == 'acute':
                cast.case_id.action_healing()
        return casts

    def write(self, vals):
        res = super().write(vals)
        if 'removal_due' in vals:
            for cast in self.filtered(lambda c: c.state == 'on'):
                cast.activity_ids.unlink()
                cast._schedule_removal_reminder()
        return res

    def _schedule_removal_reminder(self):
        for cast in self.filtered(lambda c: c.state == 'on'):
            user = cast.doctor_id.user_id or cast.case_id.doctor_id.user_id or self.env.user
            cast.activity_schedule('mail.mail_activity_data_todo', date_deadline=cast.removal_due,
                                   user_id=user.id,
                                   summary=_("Remove %(cast)s - %(patient)s", cast=cast.display_name,
                                             patient=cast.patient_id.name))

    def action_remove(self):
        for cast in self:
            if cast.state != 'on':
                raise UserError(_("This cast was already removed."))
        self.write({'state': 'removed', 'removed_date': fields.Date.context_today(self)})
        self.activity_ids.action_feedback(feedback=_("Removed"))

    def action_replace(self):
        self.ensure_one()
        new = self.copy({'applied_date': fields.Date.context_today(self),
                         'removal_due': max(self.removal_due, fields.Date.context_today(self) + timedelta(days=7))})
        self.write({'state': 'replaced', 'removed_date': fields.Date.context_today(self)})
        self.activity_ids.action_feedback(feedback=_("Replaced"))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.orthopedic.cast',
            'res_id': new.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
        }
