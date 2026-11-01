from odoo import fields, models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    dental_lab_order_id = fields.Many2one('dental.lab.order', string='Lab Order', index='btree_not_null', copy=False)
    medical_line_id = fields.Many2one('medical.treatment.line', string='Dental Treatment', index='btree_not_null', copy=False)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_dental_lab = fields.Boolean(string='Dental Lab')
