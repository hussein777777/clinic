from odoo import api, fields, models, _
from odoo.exceptions import UserError


class DentalTreatmentLine(models.Model):
    _inherit = 'medical.treatment.line'

    requires_lab = fields.Boolean(related='product_id.dental_requires_lab')
    lab_order_ids = fields.One2many('dental.lab.order', 'treatment_line_id', string='Lab Orders')
    lab_stage = fields.Selection(selection=lambda self: self.env['dental.lab.order']._fields['stage'].selection,
                                 compute='_compute_lab_stage', string='Lab Stage')
    lab_cost = fields.Monetary(compute='_compute_lab_cost', store=True)

    @api.depends('lab_order_ids.lab_cost', 'lab_order_ids.stage')
    def _compute_lab_cost(self):
        for line in self:
            line.lab_cost = sum(line.lab_order_ids.filtered(lambda o: o.stage != 'cancelled').mapped('lab_cost'))

    @api.depends('lab_order_ids.stage')
    def _compute_lab_stage(self):
        for line in self:
            orders = line.lab_order_ids.filtered(lambda o: o.stage != 'cancelled').sorted('id')
            line.lab_stage = orders[-1:].stage if orders else False

    def _prepare_lab_order_vals(self):
        self.ensure_one()
        product = self.product_id
        lab_product = product.dental_lab_product_id
        lab_partner = lab_product.seller_ids[:1].partner_id if lab_product else self.env['res.partner']
        return {
            'treatment_line_id': self.id,
            'lab_product_id': lab_product.id,
            'lab_partner_id': lab_partner.id,
            'case_type': product.dental_lab_case_type or 'crown',
            'company_id': self.company_id.id,
        }

    def action_create_lab_order(self):
        self.ensure_one()
        order = self.lab_order_ids.filtered(lambda o: o.stage != 'cancelled')[:1]
        if not order:
            vals = self._prepare_lab_order_vals()
            if vals.get('lab_product_id') and vals.get('lab_partner_id'):
                order = self.env['dental.lab.order'].create(vals)
        action = {
            'type': 'ir.actions.act_window',
            'res_model': 'dental.lab.order',
            'view_mode': 'form',
            'target': 'current',
        }
        if order:
            action['res_id'] = order.id
        else:
            action['context'] = {'default_treatment_line_id': self.id,
                                 'default_lab_product_id': self.product_id.dental_lab_product_id.id,
                                 'default_case_type': self.product_id.dental_lab_case_type or 'crown'}
        return action

    def _medical_check_before_execute(self):
        super()._medical_check_before_execute()
        for line in self.filtered('requires_lab'):
            if not line.lab_order_ids.filtered(lambda o: o.stage == 'delivered'):
                raise UserError(_("%(line)s cannot be executed before its lab work is delivered.",
                                  line=line.display_name))


class DentalTreatmentPlan(models.Model):
    _inherit = 'medical.treatment.plan'

    lab_order_count = fields.Integer(compute='_compute_lab_order_count')

    def _compute_lab_order_count(self):
        for plan in self:
            plan.lab_order_count = len(plan.line_ids.lab_order_ids)

    def action_accept(self):
        res = super().action_accept()
        for line in self.line_ids.filtered(lambda l: l.requires_lab and not l.lab_order_ids):
            vals = line._prepare_lab_order_vals()
            if vals.get('lab_product_id') and vals.get('lab_partner_id'):
                self.env['dental.lab.order'].create(vals)
        return res

    def action_view_lab_orders(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('dental_prosthodontics.action_dental_lab_order')
        action['domain'] = [('treatment_line_id', 'in', self.line_ids.ids)]
        return action
