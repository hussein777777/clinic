from odoo import _, api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    concentration_mg_ml = fields.Float(string='Concentration (mg/ml)',
                                       help='Syrups and drops: used to turn a weight-based dose into millilitres.')
    pediatric_mg_kg = fields.Float(string='Usual Dose (mg/kg per dose)')
    pediatric_max_mg = fields.Float(string='Maximum Single Dose (mg)')


class MedicalPrescriptionLine(models.Model):
    _inherit = 'medical.prescription.line'

    dose_mg_kg = fields.Float(string='mg/kg', compute='_compute_dose_mg_kg', store=True, readonly=False)
    dose_mg = fields.Float(string='Dose (mg)', compute='_compute_weight_dose', store=True, digits=(10, 1))
    dose_ml = fields.Float(string='Dose (ml)', compute='_compute_weight_dose', store=True, digits=(10, 1))
    dose_capped = fields.Boolean(compute='_compute_weight_dose', store=True,
                                 help='The weight-based dose was above the maximum single dose and was capped.')

    @api.depends('product_id')
    def _compute_dose_mg_kg(self):
        for line in self:
            if line.product_id and not line.dose_mg_kg:
                line.dose_mg_kg = line.product_id.pediatric_mg_kg

    @api.depends('dose_mg_kg', 'prescription_id.patient_weight', 'product_id')
    def _compute_weight_dose(self):
        for line in self:
            weight = line.prescription_id.patient_weight
            if not (line.dose_mg_kg and weight):
                line.dose_mg = line.dose_ml = 0.0
                line.dose_capped = False
                continue
            dose = line.dose_mg_kg * weight
            cap = line.product_id.pediatric_max_mg
            line.dose_capped = bool(cap and dose > cap)
            line.dose_mg = min(dose, cap) if cap else dose
            conc = line.product_id.concentration_mg_ml
            line.dose_ml = round(line.dose_mg / conc, 1) if conc else 0.0

    @api.onchange('dose_mg', 'dose_ml')
    def _onchange_weight_dose(self):
        text = self._weight_dose_text()
        if text:
            self.dose = text

    def _weight_dose_text(self):
        self.ensure_one()
        if self.dose_ml:
            return _("%(ml)s ml (%(mg)s mg)", ml=f"{self.dose_ml:g}", mg=f"{self.dose_mg:.0f}")
        if self.dose_mg:
            return _("%s mg", f"{self.dose_mg:.0f}")
        return False

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines.filtered(lambda l: not l.dose):
            line.dose = line._weight_dose_text()
        return lines
