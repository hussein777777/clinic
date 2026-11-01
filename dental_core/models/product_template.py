from odoo import api, fields, models

TOOTH_CONDITIONS = [
    ('sound', 'Sound'),
    ('caries', 'Caries'),
    ('filled', 'Filled'),
    ('rct', 'Root Canal Treated'),
    ('crown', 'Crown'),
    ('bridge', 'Bridge Unit'),
    ('veneer', 'Veneer'),
    ('implant', 'Implant'),
    ('missing', 'Missing'),
    ('extracted', 'Extracted'),
    ('fractured', 'Fractured'),
    ('other', 'Other'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    site_requirement = fields.Selection(selection_add=[('tooth', 'Tooth')], ondelete={'tooth': 'set default'})
    dental_requires_surface = fields.Boolean(string='Requires Surfaces',
                                             help='Fillings: the dentist must pick the surfaces (M, O, D, B, L, I).')
    dental_chart_condition = fields.Selection(TOOTH_CONDITIONS, string='Resulting Tooth Condition',
                                              help='Condition written on the tooth chart when the procedure is executed.')
    is_dental = fields.Boolean(compute='_compute_is_dental', search='_search_is_dental')

    @api.depends('specialty_id.department_id')
    def _compute_is_dental(self):
        dentistry = self.env.ref('dental_core.dept_dentistry', raise_if_not_found=False)
        for template in self:
            template.is_dental = bool(dentistry) and template.specialty_id.department_id == dentistry

    def _search_is_dental(self, operator, value):
        dentistry = self.env.ref('dental_core.dept_dentistry', raise_if_not_found=False)
        if operator in ('in', 'not in'):
            positive = (operator == 'in') == (True in value)
        else:
            positive = (operator == '=') == bool(value)
        return [('specialty_id.department_id', '=' if positive else '!=', dentistry.id if dentistry else 0)]
