from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MedicalPreauth(models.Model):
    _name = 'medical.preauth'
    _description = 'Insurance Pre-authorization'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    policy_id = fields.Many2one('medical.patient.policy', required=True, index=True, tracking=True)
    patient_id = fields.Many2one(related='policy_id.patient_id', store=True)
    insurer_id = fields.Many2one(related='policy_id.insurer_id', store=True)
    member_no = fields.Char(related='policy_id.member_no')
    plan_id = fields.Many2one('medical.treatment.plan', string='Treatment Plan', index=True)
    line_ids = fields.One2many('medical.preauth.line', 'preauth_id', string='Procedures', copy=True)
    request_date = fields.Date(default=fields.Date.context_today, tracking=True)
    response_date = fields.Date(tracking=True, copy=False)
    approval_no = fields.Char(string='Approval No.', tracking=True, copy=False)
    expiry_date = fields.Date(tracking=True, copy=False)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('approved', 'Approved'),
        ('partial', 'Partially Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    company_id = fields.Many2one(related='policy_id.company_id', store=True)
    currency_id = fields.Many2one(related='policy_id.currency_id')
    requested_amount = fields.Monetary(compute='_compute_amounts', store=True)
    approved_amount = fields.Monetary(compute='_compute_amounts', store=True)
    note = fields.Text()

    @api.depends('line_ids.requested_amount', 'line_ids.approved_amount')
    def _compute_amounts(self):
        for pa in self:
            pa.requested_amount = sum(pa.line_ids.mapped('requested_amount'))
            pa.approved_amount = sum(pa.line_ids.mapped('approved_amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.preauth') or _('New')
        return super().create(vals_list)

    def action_send(self):
        for pa in self:
            if not pa.line_ids:
                raise UserError(_("Add at least one procedure."))
        self.write({'state': 'sent'})

    def action_record_response(self):
        """Store the insurer reply: approved amounts are typed on the lines beforehand."""
        for pa in self:
            if pa.state not in ('draft', 'sent'):
                raise UserError(_("The response was already recorded."))
            if pa.approved_amount and not pa.approval_no:
                raise UserError(_("Enter the insurer approval number."))
            if pa.currency_id.is_zero(pa.approved_amount):
                state = 'rejected'
            elif all(not pa.currency_id.compare_amounts(l.approved_amount, l.requested_amount) for l in pa.line_ids):
                state = 'approved'
            else:
                state = 'partial'
            pa.write({'state': state, 'response_date': pa.response_date or fields.Date.context_today(self)})
        self.line_ids.treatment_line_id._recompute_insurance()

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        self.line_ids.treatment_line_id._recompute_insurance()

    def action_draft(self):
        self.filtered(lambda p: p.state in ('cancelled', 'rejected')).write({'state': 'draft'})


class MedicalPreauthLine(models.Model):
    _name = 'medical.preauth.line'
    _description = 'Pre-authorization Line'

    preauth_id = fields.Many2one('medical.preauth', required=True, ondelete='cascade', index=True)
    treatment_line_id = fields.Many2one('medical.treatment.line', required=True, index=True, ondelete='cascade')
    product_id = fields.Many2one(related='treatment_line_id.product_id')
    insurance_code = fields.Char(related='treatment_line_id.product_id.insurance_code')
    site_label = fields.Char(related='treatment_line_id.site_label')
    currency_id = fields.Many2one(related='preauth_id.currency_id')
    requested_amount = fields.Monetary(required=True)
    approved_amount = fields.Monetary()
    is_valid = fields.Boolean(compute='_compute_is_valid',
                              help='Approved, not expired and the request is not cancelled.')

    @api.depends('preauth_id.state', 'preauth_id.expiry_date', 'approved_amount')
    def _compute_is_valid(self):
        today = fields.Date.context_today(self)
        for line in self:
            pa = line.preauth_id
            line.is_valid = bool(
                pa.state in ('approved', 'partial')
                and line.approved_amount > 0
                and (not pa.expiry_date or pa.expiry_date >= today)
            )
