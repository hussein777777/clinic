from odoo import fields, models

MATERIALS = [
    ('composite', 'Composite'),
    ('amalgam', 'Amalgam'),
    ('gic', 'Glass Ionomer'),
    ('ceramic', 'Ceramic'),
    ('temporary', 'Temporary'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    dental_price_per_extra_surface = fields.Monetary(
        string='Price per Extra Surface',
        help='Added to the price for every surface beyond the first (e.g. MOD = 2 extra surfaces).')
    dental_default_material = fields.Selection(MATERIALS, string='Default Material')
