from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from .product_template import TOOTH_CONDITIONS

VALID_SURFACES = set('MODBLI')


class DentalChartLine(models.Model):
    _name = 'dental.chart.line'
    _description = 'Tooth Chart Entry'
    _order = 'date desc, id desc'

    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade',
                                 domain=[('is_patient', '=', True)])
    tooth_id = fields.Many2one('dental.tooth', required=True, index=True)
    surfaces = fields.Char(help='Any of M, O, D, B, L, I')
    condition = fields.Selection(TOOTH_CONDITIONS, required=True, default='caries')
    date = fields.Date(default=fields.Date.context_today, required=True)
    treatment_line_id = fields.Many2one('medical.treatment.line', string='Source Treatment', ondelete='set null')
    doctor_id = fields.Many2one('hr.employee', string='Dentist')
    note = fields.Char()

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
