from odoo import fields, models

LAB_CASE_TYPES = [
    ('crown', 'Crown'),
    ('bridge', 'Bridge'),
    ('veneer', 'Veneer'),
    ('inlay', 'Inlay / Onlay'),
    ('implant_crown', 'Implant Crown'),
    ('denture_full', 'Full Denture'),
    ('denture_partial', 'Partial Denture'),
    ('night_guard', 'Night Guard'),
    ('other', 'Other'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    dental_requires_lab = fields.Boolean(string='Requires Lab Work',
                                         help='Executing this procedure requires a delivered lab order.')
    dental_lab_product_id = fields.Many2one('product.product', string='Default Lab Service',
                                            domain=[('purchase_ok', '=', True)],
                                            help='Service purchased from the lab for this procedure.')
    dental_lab_case_type = fields.Selection(LAB_CASE_TYPES, string='Lab Case Type')
