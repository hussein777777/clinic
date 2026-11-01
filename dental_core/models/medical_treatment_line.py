from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from .dental_chart_line import VALID_SURFACES


class MedicalTreatmentLine(models.Model):
    _inherit = 'medical.treatment.line'

    tooth_id = fields.Many2one('dental.tooth', string='Tooth')
    surfaces = fields.Char(help='Any of M, O, D, B, L, I')

    @api.depends('body_site_id', 'laterality', 'tooth_id', 'surfaces')
    def _compute_site(self):
        return super()._compute_site()

    def _site_parts(self):
        if not self.tooth_id:
            return super()._site_parts()
        label = _("tooth %s", self.tooth_id.code) + (f" {self.surfaces}" if self.surfaces else '')
        return label, f"tooth:{self.tooth_id.code}"

    @api.constrains('surfaces')
    def _check_surfaces(self):
        for line in self.filtered('surfaces'):
            if not set(line.surfaces.upper()) <= VALID_SURFACES:
                raise ValidationError(_("Surfaces can only contain the letters M, O, D, B, L, I."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('surfaces'):
                vals['surfaces'] = vals['surfaces'].upper()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('surfaces'):
            vals['surfaces'] = vals['surfaces'].upper()
        return super().write(vals)

    def _protected_fields(self):
        return super()._protected_fields() | {'tooth_id', 'surfaces'}

    def _site_missing(self):
        product = self.product_id
        if product.site_requirement == 'tooth':
            if not self.tooth_id:
                return _("%(proc)s requires a tooth.", proc=product.display_name)
            if product.dental_requires_surface and not self.surfaces:
                return _("%(proc)s on tooth %(tooth)s requires the surfaces.",
                         proc=product.display_name, tooth=self.tooth_id.code)
            return False
        return super()._site_missing()

    def _prepare_chart_line_vals(self):
        self.ensure_one()
        return {
            'patient_id': self.patient_id.id,
            'tooth_id': self.tooth_id.id,
            'surfaces': self.surfaces,
            'condition': self.product_id.dental_chart_condition,
            'date': self.date_executed,
            'doctor_id': self.doctor_id.id,
            'treatment_line_id': self.id,
        }

    def _after_execute(self):
        res = super()._after_execute()
        chart_vals = [line._prepare_chart_line_vals() for line in self
                      if line.tooth_id and line.product_id.dental_chart_condition]
        if chart_vals:
            self.env['dental.chart.line'].sudo().create(chart_vals)
        return res
