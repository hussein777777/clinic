from odoo import api, fields, models


class MedicalCareContract(models.Model):
    _inherit = 'medical.care.contract'

    care_type = fields.Selection(selection_add=[('orthodontics', 'Orthodontics')],
                                 ondelete={'orthodontics': 'set default'})
    appliance_type = fields.Selection([
        ('metal', 'Metal Brackets'),
        ('ceramic', 'Ceramic Brackets'),
        ('lingual', 'Lingual'),
        ('aligners', 'Clear Aligners'),
        ('removable', 'Removable Appliance'),
        ('retainer', 'Retainer Only'),
    ], default='metal', tracking=True)
    arch = fields.Selection([('both', 'Both Arches'), ('upper', 'Upper'), ('lower', 'Lower')], default='both')

    @api.onchange('care_type')
    def _onchange_care_type_ortho(self):
        if self.care_type == 'orthodontics':
            self.billing_mode = 'monthly'
            self.session_product_id = self.env.ref('dental_orthodontics.product_ortho_adjustment', raise_if_not_found=False)
            if self.est_months <= 1:
                self.est_months = 18
