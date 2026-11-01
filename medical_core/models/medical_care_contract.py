from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class MedicalCareContract(models.Model):
    """A prepaid treatment program: orthodontics, physiotherapy course, laser package, antenatal care...

    Billed once as a package (down payment + installments). Revenue is earned month by month
    (`monthly`) or session by session (`sessions`); medical_account does the accounting.
    """
    _name = 'medical.care.contract'
    _description = 'Care Program'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start_date desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    care_type = fields.Selection([('program', 'Care Program')], default='program', required=True, tracking=True)
    patient_id = fields.Many2one('res.partner', required=True, index=True, tracking=True,
                                 domain=[('is_patient', '=', True)])
    doctor_id = fields.Many2one('hr.employee', string='Doctor', required=True, tracking=True,
                                domain=[('is_practitioner', '=', True)])
    department_id = fields.Many2one('medical.department', compute='_compute_department_id', store=True,
                                    readonly=False, precompute=True, index=True)
    product_id = fields.Many2one('product.product', string='Package', required=True,
                                 domain=[('is_medical_procedure', '=', True), ('procedure_type', '=', 'package')])
    billing_mode = fields.Selection([
        ('monthly', 'Earned monthly'),
        ('sessions', 'Earned per session'),
    ], default='monthly', required=True, tracking=True)
    session_product_id = fields.Many2one('product.product', string='Session Procedure',
                                         domain=[('is_medical_procedure', '=', True)],
                                         help='Procedure booked on each visit of the program (price 0, covered by the package).')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    package_price = fields.Monetary(compute='_compute_package_price', store=True, readonly=False, precompute=True,
                                    tracking=True)
    down_payment = fields.Monetary(compute='_compute_down_payment', store=True, readonly=False, precompute=True,
                                   tracking=True)
    installment_count = fields.Integer(default=0, tracking=True)
    installment_amount = fields.Monetary(compute='_compute_installment_amount')
    start_date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    est_months = fields.Integer(string='Duration (months)', default=1, required=True, tracking=True)
    end_date = fields.Date(compute='_compute_end_date', store=True)
    session_count = fields.Integer(string='Sessions', default=0, tracking=True)
    sessions_used = fields.Integer(compute='_compute_sessions', store=True)
    sessions_remaining = fields.Integer(compute='_compute_sessions', store=True)
    months_elapsed = fields.Integer(compute='_compute_progress')
    progress = fields.Float(compute='_compute_progress')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('maintenance', 'Retention / Maintenance'),
        ('done', 'Completed'),
        ('terminated', 'Terminated'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    visit_ids = fields.One2many('medical.visit', 'care_contract_id', string='Visits')
    line_ids = fields.One2many('medical.treatment.line', 'care_contract_id', string='Sessions Done')
    visit_count = fields.Integer(compute='_compute_visit_count')
    last_visit_date = fields.Datetime(compute='_compute_visit_count')
    note = fields.Html()

    @api.depends('doctor_id', 'product_id')
    def _compute_department_id(self):
        for contract in self:
            contract.department_id = contract.product_id.medical_department_id or \
                contract.doctor_id.sudo().medical_department_ids[:1] or contract.department_id

    @api.depends('product_id')
    def _compute_package_price(self):
        for contract in self:
            if contract.state == 'draft' or not contract.package_price:
                contract.package_price = contract.product_id.lst_price

    @api.depends('package_price', 'installment_count')
    def _compute_down_payment(self):
        for contract in self:
            if not contract.installment_count:
                contract.down_payment = contract.package_price
            elif contract.down_payment > contract.package_price:
                contract.down_payment = 0.0

    @api.depends('package_price', 'down_payment', 'installment_count')
    def _compute_installment_amount(self):
        for contract in self:
            rest = contract.package_price - contract.down_payment
            contract.installment_amount = contract.currency_id.round(rest / contract.installment_count) \
                if contract.installment_count else rest

    @api.depends('start_date', 'est_months')
    def _compute_end_date(self):
        for contract in self:
            contract.end_date = contract.start_date + relativedelta(months=contract.est_months) \
                if contract.start_date else False

    @api.depends('line_ids.state', 'line_ids.product_id', 'session_product_id', 'session_count')
    def _compute_sessions(self):
        for contract in self:
            done = contract.line_ids.filtered(lambda l: l.state == 'executed' and (
                not contract.session_product_id or l.product_id == contract.session_product_id))
            contract.sessions_used = len(done)
            contract.sessions_remaining = max(contract.session_count - len(done), 0)

    @api.depends('start_date', 'est_months', 'sessions_used', 'session_count', 'billing_mode', 'state')
    def _compute_progress(self):
        today = fields.Date.context_today(self)
        for contract in self:
            if contract.start_date and contract.state != 'draft':
                delta = relativedelta(today, contract.start_date)
                contract.months_elapsed = max(delta.years * 12 + delta.months, 0)
            else:
                contract.months_elapsed = 0
            if contract.billing_mode == 'sessions':
                contract.progress = 100.0 * contract.sessions_used / contract.session_count if contract.session_count else 0.0
            else:
                contract.progress = min(100.0, 100.0 * contract.months_elapsed / contract.est_months) \
                    if contract.est_months else 0.0

    def _compute_visit_count(self):
        for contract in self:
            done = contract.visit_ids.filtered(lambda v: v.state in ('done', 'invoiced'))
            contract.visit_count = len(done)
            contract.last_visit_date = max(done.mapped('start')) if done else False

    @api.constrains('down_payment', 'package_price', 'installment_count', 'est_months', 'session_count', 'billing_mode')
    def _check_amounts(self):
        for contract in self:
            if contract.down_payment < 0 or contract.down_payment > contract.package_price:
                raise ValidationError(_("The down payment must be between 0 and the package price."))
            if contract.installment_count < 0 or contract.est_months <= 0:
                raise ValidationError(_("Installments and duration must be positive."))
            if contract.installment_count == 0 and contract.down_payment != contract.package_price:
                raise ValidationError(_("Without installments, the down payment must cover the whole package."))
            if contract.billing_mode == 'sessions' and contract.session_count <= 0:
                raise ValidationError(_("A per-session program needs the number of sessions."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.care.contract') or _('New')
        return super().create(vals_list)

    # ----------------------------------------------------------------- workflow
    def action_start(self):
        for contract in self:
            if contract.state != 'draft':
                raise UserError(_("Only draft programs can be started."))
        self.write({'state': 'active'})

    def action_maintenance(self):
        self.filtered(lambda c: c.state == 'active').write({'state': 'maintenance'})

    def action_complete(self):
        self.filtered(lambda c: c.state in ('active', 'maintenance')).write({'state': 'done'})

    def action_terminate(self):
        """Clinical termination. medical_account extends it with the financial settlement."""
        self.filtered(lambda c: c.state in ('active', 'maintenance')).write({'state': 'terminated'})

    def _session_visit_vals(self):
        self.ensure_one()
        product = self.session_product_id
        return {
            'patient_id': self.patient_id.id,
            'doctor_id': self.doctor_id.id,
            'department_id': self.department_id.id,
            'care_contract_id': self.id,
            'visit_type': 'session' if self.billing_mode == 'sessions' else 'follow_up',
            'line_ids': [(0, 0, {'product_id': product.id, 'care_contract_id': self.id})] if product else [],
        }

    def action_new_session_visit(self):
        self.ensure_one()
        if self.state not in ('active', 'maintenance'):
            raise UserError(_("Start the program first."))
        if self.billing_mode == 'sessions' and self.sessions_remaining <= 0:
            raise UserError(_("No sessions left in %s.", self.name))
        visit = self.env['medical.visit'].create(self._session_visit_vals())
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.visit',
            'res_id': visit.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_view_visits(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('medical_core.action_medical_visit_history')
        action.update({
            'domain': [('care_contract_id', '=', self.id)],
            'context': {'default_care_contract_id': self.id, 'default_patient_id': self.patient_id.id,
                        'default_doctor_id': self.doctor_id.id},
        })
        return action
