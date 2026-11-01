from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command

from .product_template import LAB_CASE_TYPES

STAGES = [
    ('draft', 'Draft'),
    ('sent', 'Sent to Lab'),
    ('received', 'Received from Lab'),
    ('try_in', 'Try-in'),
    ('delivered', 'Delivered'),
    ('cancelled', 'Cancelled'),
]


class DentalLabOrder(models.Model):
    _name = 'dental.lab.order'
    _description = 'Dental Lab Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    treatment_line_id = fields.Many2one('medical.treatment.line', string='Treatment', required=True,
                                        ondelete='restrict', index=True)
    patient_id = fields.Many2one(related='treatment_line_id.patient_id', store=True)
    doctor_id = fields.Many2one(related='treatment_line_id.doctor_id', store=True)
    tooth_id = fields.Many2one(related='treatment_line_id.tooth_id', store=True)
    lab_partner_id = fields.Many2one('res.partner', string='Lab', required=True, tracking=True,
                                     domain=[('is_dental_lab', '=', True)])
    lab_product_id = fields.Many2one('product.product', string='Lab Service', required=True,
                                     domain=[('purchase_ok', '=', True)])
    case_type = fields.Selection(LAB_CASE_TYPES, required=True, default='crown')
    material = fields.Char(help='Zirconia, E.max, PFM...')
    shade = fields.Char()
    units = fields.Integer(default=1, help='Number of units (e.g. a 3-unit bridge).')
    due_date = fields.Date(tracking=True)
    stage = fields.Selection(STAGES, default='draft', required=True, tracking=True, copy=False, index=True)
    is_late = fields.Boolean(compute='_compute_is_late', search='_search_is_late')
    remake_count = fields.Integer(copy=False, readonly=True)
    remake_reason = fields.Text(copy=False)
    purchase_line_ids = fields.One2many('purchase.order.line', 'dental_lab_order_id', string='Purchase Lines', readonly=True)
    purchase_ids = fields.Many2many('purchase.order', compute='_compute_purchase', string='Purchase Orders')
    purchase_count = fields.Integer(compute='_compute_purchase')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    lab_cost = fields.Monetary(compute='_compute_lab_cost', store=True,
                               help='Billed amount when the vendor bill is posted, otherwise the PO amount.')
    lab_cost_is_estimate = fields.Boolean(compute='_compute_lab_cost', store=True)
    note = fields.Text()

    @api.depends('due_date', 'stage')
    def _compute_is_late(self):
        today = fields.Date.context_today(self)
        for order in self:
            order.is_late = bool(order.due_date and order.due_date < today and order.stage in ('draft', 'sent'))

    def _search_is_late(self, operator, value):
        if operator in ('in', 'not in'):
            value = True in value
            operator = '=' if operator == 'in' else '!='
        if operator not in ('=', '!=') or not isinstance(value, bool):
            return NotImplemented
        domain = [('due_date', '<', fields.Date.context_today(self)), ('stage', 'in', ('draft', 'sent'))]
        return domain if (operator == '=') == value else ['!', *domain]

    @api.depends('purchase_line_ids')
    def _compute_purchase(self):
        for order in self:
            order.purchase_ids = order.purchase_line_ids.order_id
            order.purchase_count = len(order.purchase_ids)

    @api.depends('purchase_line_ids.price_subtotal', 'purchase_line_ids.state',
                 'purchase_line_ids.invoice_lines.parent_state', 'purchase_line_ids.invoice_lines.price_subtotal')
    def _compute_lab_cost(self):
        for order in self:
            total, estimate = 0.0, False
            for pol in order.purchase_line_ids.filtered(lambda l: l.state in ('purchase', 'done')):
                billed = pol.invoice_lines.filtered(lambda l: l.parent_state == 'posted')
                if billed:
                    total += sum(l.price_subtotal * (-1 if l.move_id.move_type == 'in_refund' else 1) for l in billed)
                else:
                    total += pol.price_subtotal
                    estimate = True
            order.lab_cost = total
            order.lab_cost_is_estimate = estimate

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('dental.lab.order') or _('New')
        return super().create(vals_list)

    # ---------------------------------------------------------------- purchase
    def _prepare_purchase_line_vals(self):
        self.ensure_one()
        line = self.treatment_line_id
        name = "%s - %s - %s" % (
            self.lab_product_id.display_name,
            line.patient_id.patient_code or line.patient_id.name,
            _("tooth %s", line.tooth_id.code) if line.tooth_id else self.case_type,
        )
        if self.shade:
            name += " - " + _("shade %s", self.shade)
        vals = {
            'product_id': self.lab_product_id.id,
            'name': name,
            'product_qty': self.units or 1,
            'dental_lab_order_id': self.id,
            'medical_line_id': line.id,
            'date_planned': fields.Datetime.to_datetime(self.due_date) if self.due_date else fields.Datetime.now(),
        }
        seller = self.lab_product_id._select_seller(partner_id=self.lab_partner_id, quantity=self.units or 1)
        if seller:
            vals['price_unit'] = seller.price
        distribution = line._medical_analytic_distribution() if hasattr(line, '_medical_analytic_distribution') else False
        if distribution:
            vals['analytic_distribution'] = distribution
        return vals

    def _create_purchase_order(self):
        self.ensure_one()
        po = self.env['purchase.order'].create({
            'partner_id': self.lab_partner_id.id,
            'origin': self.name,
            'company_id': self.company_id.id,
            'order_line': [Command.create(self._prepare_purchase_line_vals())],
        })
        po.button_confirm()
        return po

    # ----------------------------------------------------------------- stages
    def action_send_to_lab(self):
        for order in self:
            if order.stage != 'draft':
                raise UserError(_("Only draft lab orders can be sent."))
            if not order.purchase_line_ids:
                order._create_purchase_order()
            order.stage = 'sent'

    def action_mark_received(self):
        for order in self.filtered(lambda o: o.stage == 'sent'):
            for pol in order.purchase_line_ids.filtered(lambda l: l.state in ('purchase', 'done')):
                if pol.qty_received < pol.product_qty:
                    pol.qty_received_manual = pol.product_qty
            order.stage = 'received'

    def action_try_in(self):
        self.filtered(lambda o: o.stage == 'received').write({'stage': 'try_in'})

    def action_deliver(self):
        for order in self:
            if order.stage not in ('received', 'try_in'):
                raise UserError(_("The work must be received from the lab before delivery."))
        self.write({'stage': 'delivered'})

    def action_cancel(self):
        for order in self:
            if order.stage == 'delivered':
                raise UserError(_("Delivered lab orders cannot be cancelled."))
            pos = order.purchase_line_ids.order_id.filtered(lambda p: p.state in ('draft', 'sent', 'to approve'))
            pos.button_cancel()
        self.write({'stage': 'cancelled'})

    def action_remake(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Remake'),
            'res_model': 'dental.lab.remake.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_lab_order_id': self.id},
        }

    def _do_remake(self, fault, reason):
        self.ensure_one()
        if fault == 'clinic':
            # the clinic pays for a new piece: new purchase order on the same case
            self._create_purchase_order()
        self.write({
            'stage': 'sent',
            'remake_count': self.remake_count + 1,
            'remake_reason': ((self.remake_reason + '\n') if self.remake_reason else '')
                             + f"[{fields.Date.today()}] {dict(self._remake_faults()).get(fault)}: {reason}",
        })
        self.message_post(body=_("Remake requested (%(fault)s): %(reason)s",
                                 fault=dict(self._remake_faults()).get(fault), reason=reason))

    @api.model
    def _remake_faults(self):
        return [('lab', _('Lab fault (no extra cost)')), ('clinic', _('Clinic fault (new lab charge)'))]

    def action_view_purchase(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('purchase.purchase_rfq')
        action['domain'] = [('id', 'in', self.purchase_ids.ids)]
        action['context'] = {}
        if len(self.purchase_ids) == 1:
            action.update({'view_mode': 'form', 'res_id': self.purchase_ids.id,
                           'views': [(self.env.ref('purchase.purchase_order_form').id, 'form')]})
        return action


class DentalLabRemakeWizard(models.TransientModel):
    _name = 'dental.lab.remake.wizard'
    _description = 'Lab order remake'

    lab_order_id = fields.Many2one('dental.lab.order', required=True)
    fault = fields.Selection(selection=lambda self: self.env['dental.lab.order']._remake_faults(),
                             required=True, default='lab')
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.lab_order_id._do_remake(self.fault, self.reason)
        return {'type': 'ir.actions.act_window_close'}
