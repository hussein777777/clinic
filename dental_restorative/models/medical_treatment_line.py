from odoo import api, fields, models

from .product_template import MATERIALS


class MedicalTreatmentLine(models.Model):
    _inherit = 'medical.treatment.line'

    material = fields.Selection(MATERIALS, compute='_compute_material', store=True, readonly=False)

    @api.depends('product_id')
    def _compute_material(self):
        for line in self:
            if not line.material:
                line.material = line.product_id.dental_default_material

    @api.depends('product_id', 'visit_id', 'plan_id', 'care_contract_id', 'surfaces')
    def _compute_price_unit(self):
        return super()._compute_price_unit()

    def _get_medical_price(self):
        price = super()._get_medical_price()
        extra = self.product_id.dental_price_per_extra_surface
        if extra and self.surfaces and price:
            price += extra * max(len(set(self.surfaces.upper())) - 1, 0)
        return price
