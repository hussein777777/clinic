from odoo import fields, models


class DentalTooth(models.Model):
    _name = 'dental.tooth'
    _description = 'Tooth (FDI notation)'
    _order = 'is_primary, quadrant, position'
    _rec_name = 'code'

    code = fields.Char(required=True, help='FDI two-digit code, e.g. 11, 36, 85')
    name = fields.Char(required=True, translate=True)
    quadrant = fields.Integer(required=True, help='1-4 permanent, 5-8 primary')
    position = fields.Integer(required=True, help='1 = central incisor ... 8 = third molar')
    is_primary = fields.Boolean(string='Primary (Deciduous)')
    arch = fields.Selection([('upper', 'Upper'), ('lower', 'Lower')], required=True)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code)', 'The FDI code must be unique.')

    def _compute_display_name(self):
        for tooth in self:
            tooth.display_name = f"{tooth.code} - {tooth.name}"
