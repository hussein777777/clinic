from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .medical_observation import FLAGS


class MedicalOrder(models.Model):
    _name = 'medical.order'
    _description = 'Lab / Imaging Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, domain=[('is_patient', '=', True)],
                                 tracking=True)
    visit_id = fields.Many2one('medical.visit', index='btree_not_null', ondelete='set null')
    doctor_id = fields.Many2one('hr.employee', string='Requested by', required=True,
                                domain=[('is_practitioner', '=', True)],
                                default=lambda self: self.env.user.employee_id.filtered('is_practitioner'))
    department_id = fields.Many2one('medical.department', compute='_compute_department_id', store=True,
                                    readonly=False, precompute=True)
    order_type = fields.Selection([('lab', 'Laboratory'), ('imaging', 'Imaging'), ('test', 'Other Diagnostic Test')],
                                  default='lab', required=True, tracking=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    priority = fields.Selection([('routine', 'Routine'), ('urgent', 'Urgent')], default='routine', required=True,
                                tracking=True)
    performer = fields.Selection([('internal', 'In the center'), ('external', 'External lab / center')],
                                 default='internal', required=True,
                                 help='Internal tests are billed on the visit; external ones only record the results.')
    external_partner_id = fields.Many2one('res.partner', string='External Lab / Center')
    clinical_info = fields.Text(string='Clinical Information', help='Why the test is requested.')
    line_ids = fields.One2many('medical.order.line', 'order_id', string='Tests', copy=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('requested', 'Requested'),
        ('in_progress', 'Sample Taken / In Progress'),
        ('done', 'Results Ready'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    result_date = fields.Datetime(readonly=True, copy=False)
    has_abnormal = fields.Boolean(compute='_compute_has_abnormal', store=True, string='Abnormal Result')
    attachment_ids = fields.Many2many('ir.attachment', string='Reports & Images')
    conclusion = fields.Html(string='Report / Conclusion')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    @api.depends('visit_id', 'doctor_id')
    def _compute_department_id(self):
        for order in self:
            order.department_id = order.visit_id.department_id or \
                order.doctor_id.sudo().medical_department_ids[:1] or order.department_id

    @api.depends('line_ids.result_ids.flag')
    def _compute_has_abnormal(self):
        for order in self:
            order.has_abnormal = any(f and f != 'normal' for f in order.line_ids.result_ids.mapped('flag'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.order') or _('New')
        return super().create(vals_list)

    def action_request(self):
        for order in self:
            if not order.line_ids:
                raise UserError(_("Add at least one test."))
            if order.performer == 'external' and not order.external_partner_id:
                raise UserError(_("Choose the external lab or imaging center."))
        self.write({'state': 'requested'})
        self.filtered(lambda o: o.performer == 'internal')._create_billing_lines()

    def _billing_visit(self):
        """Open visit that carries the test charges: the requesting visit, or a new test visit."""
        self.ensure_one()
        if self.visit_id and self.visit_id.state in ('draft', 'confirmed', 'checked_in', 'in_consult'):
            return self.visit_id
        return self.env['medical.visit'].create({
            'patient_id': self.patient_id.id,
            'doctor_id': self.doctor_id.id,
            'department_id': self.department_id.id,
            'visit_type': 'procedure',
            'start': fields.Datetime.now(),
            'state': 'checked_in',
        })

    def _create_billing_lines(self):
        """Internal tests are charged as treatment lines; reception collects when the visit is closed."""
        Line = self.env['medical.treatment.line']
        for order in self:
            lines = order.line_ids.filtered(lambda l: not l.treatment_line_id)
            if not lines:
                continue
            visit = order._billing_visit()
            for line in lines:
                line.treatment_line_id = Line.create({
                    'visit_id': visit.id,
                    'doctor_id': order.doctor_id.id,
                    'product_id': line.product_id.id,
                    'body_site_id': line.body_site_id.id,
                    'laterality': line.laterality,
                    'note': order.name,
                })

    def action_start(self):
        self.filtered(lambda o: o.state == 'requested').write({'state': 'in_progress'})

    def action_done(self):
        for order in self:
            if order.state not in ('requested', 'in_progress'):
                raise UserError(_("Only requested orders can receive results."))
            has_results = order.line_ids.result_ids.filtered('has_value') or any(order.line_ids.mapped('result_text'))
            if not has_results and not order.conclusion and not order.attachment_ids:
                raise UserError(_("Enter the results, a report or attach the result file."))
        self.line_ids.result_ids._create_observations()
        self.write({'state': 'done', 'result_date': fields.Datetime.now()})
        for order in self.filtered('has_abnormal'):
            order.activity_schedule('mail.mail_activity_data_todo', user_id=order.doctor_id.user_id.id or self.env.uid,
                                    summary=_("Abnormal result: %s", order.name))

    def action_cancel(self):
        if any(o.state == 'done' for o in self):
            raise UserError(_("Orders with results cannot be cancelled."))
        self.line_ids.treatment_line_id.filtered(lambda l: l.state in ('planned', 'scheduled')).action_cancel()
        self.write({'state': 'cancelled'})

    def action_print(self):
        return self.env.ref('medical_core.action_report_medical_order').report_action(self)


class MedicalOrderLine(models.Model):
    _name = 'medical.order.line'
    _description = 'Ordered Test'
    _order = 'order_id, sequence, id'

    order_id = fields.Many2one('medical.order', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', string='Test', required=True,
                                 domain=[('is_medical_procedure', '=', True), ('procedure_type', 'in', ('lab', 'imaging', 'test'))])
    body_site_id = fields.Many2one('medical.body.site', string='Site')
    laterality = fields.Selection([('right', 'Right'), ('left', 'Left'), ('bilateral', 'Both Sides')], string='Side')
    note = fields.Char()
    treatment_line_id = fields.Many2one('medical.treatment.line', readonly=True, copy=False)
    result_ids = fields.One2many('medical.order.result', 'order_line_id', string='Results')
    result_text = fields.Text(string='Findings')

    @api.depends('product_id', 'body_site_id', 'laterality')
    def _compute_display_name(self):
        sides = dict(self._fields['laterality']._description_selection(self.env))
        for line in self:
            parts = [line.product_id.name, line.body_site_id.name, sides.get(line.laterality)]
            line.display_name = ' '.join(p for p in parts if p)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._add_result_rows()
        return lines

    def _add_result_rows(self):
        for line in self:
            missing = line.product_id.result_type_ids - line.result_ids.type_id
            line.result_ids = [(0, 0, {'type_id': t.id}) for t in missing]


class MedicalOrderResult(models.Model):
    _name = 'medical.order.result'
    _description = 'Test Result'
    _order = 'order_line_id, id'

    order_line_id = fields.Many2one('medical.order.line', required=True, ondelete='cascade', index=True)
    order_id = fields.Many2one(related='order_line_id.order_id', store=True, index=True)
    patient_id = fields.Many2one(related='order_line_id.order_id.patient_id', store=True)
    type_id = fields.Many2one('medical.observation.type', string='Component', required=True)
    unit = fields.Char(related='type_id.unit')
    normal_range = fields.Char(compute='_compute_normal_range')
    value = fields.Float(digits=(16, 3))
    has_value = fields.Boolean(string='Done', help='Untick when the component was not measured.')
    flag = fields.Selection(FLAGS, compute='_compute_flag', store=True)
    observation_id = fields.Many2one('medical.observation', readonly=True, copy=False)

    @api.depends('type_id.normal_min', 'type_id.normal_max')
    def _compute_normal_range(self):
        for result in self:
            t = result.type_id
            result.normal_range = f"{t.normal_min:g} – {t.normal_max:g}" if (t.normal_min or t.normal_max) else ''

    @api.depends('value', 'has_value', 'type_id')
    def _compute_flag(self):
        for result in self:
            result.flag = result.type_id._flag(result.value) if result.has_value and result.type_id else False

    @api.onchange('value')
    def _onchange_value(self):
        if self.value:
            self.has_value = True

    def write(self, vals):
        if 'value' in vals and 'has_value' not in vals:
            vals['has_value'] = True
        return super().write(vals)

    def _create_observations(self):
        Observation = self.env['medical.observation'].sudo()
        for result in self.filtered(lambda r: r.has_value and not r.observation_id):
            order = result.order_id
            result.observation_id = Observation.create({
                'patient_id': order.patient_id.id,
                'type_id': result.type_id.id,
                'value': result.value,
                'date': order.date,
                'visit_id': order.visit_id.id,
                'source': 'order',
                'note': order.name,
            })
