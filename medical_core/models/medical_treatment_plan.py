from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MedicalTreatmentPlan(models.Model):
    _name = 'medical.treatment.plan'
    _description = 'Treatment Plan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, tracking=True,
                                 domain=[('is_patient', '=', True)])
    doctor_id = fields.Many2one('hr.employee', string='Doctor', tracking=True, domain=[('is_practitioner', '=', True)])
    department_id = fields.Many2one('medical.department', string='Department', compute='_compute_department_id',
                                    store=True, readonly=False, precompute=True, index=True)
    date = fields.Date(default=fields.Date.context_today, required=True)
    line_ids = fields.One2many('medical.treatment.line', 'plan_id', string='Treatments', copy=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('presented', 'Presented'),
        ('accepted', 'Accepted'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    amount_total = fields.Monetary(compute='_compute_amounts', store=True)
    amount_executed = fields.Monetary(compute='_compute_amounts', store=True)
    progress = fields.Float(compute='_compute_amounts', store=True, help='Executed share of the plan (%)')
    note = fields.Html()

    @api.depends('doctor_id')
    def _compute_department_id(self):
        for plan in self:
            if not plan.department_id and plan.doctor_id:
                plan.department_id = plan.doctor_id.sudo().medical_department_ids[:1]

    @api.depends('line_ids.price_subtotal', 'line_ids.state')
    def _compute_amounts(self):
        for plan in self:
            active = plan.line_ids.filtered(lambda l: l.state != 'cancelled')
            plan.amount_total = sum(active.mapped('price_subtotal'))
            plan.amount_executed = sum(active.filtered(lambda l: l.state == 'executed').mapped('price_subtotal'))
            plan.progress = 100.0 * plan.amount_executed / plan.amount_total if plan.amount_total else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.treatment.plan') or _('New')
        return super().create(vals_list)

    def action_present(self):
        if any(not plan.line_ids for plan in self):
            raise UserError(_("Add at least one treatment before presenting the plan."))
        self.write({'state': 'presented'})

    def action_accept(self):
        self.write({'state': 'accepted'})

    def action_cancel(self):
        for plan in self:
            plan.line_ids.filtered(lambda l: l.state in ('planned', 'scheduled')).action_cancel()
        self.write({'state': 'cancelled'})

    def action_draft(self):
        self.filtered(lambda p: p.state in ('presented', 'cancelled')).write({'state': 'draft'})

    def _update_progress(self):
        for plan in self.filtered(lambda p: p.state in ('draft', 'presented', 'accepted', 'in_progress')):
            states = set(plan.line_ids.mapped('state'))
            if states and states <= {'executed', 'cancelled'} and 'executed' in states:
                plan.state = 'done'
            elif 'executed' in states:
                plan.state = 'in_progress'
