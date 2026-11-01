from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    chart_line_ids = fields.One2many('dental.chart.line', 'patient_id', string='Tooth Chart')
    dental_chart_data = fields.Json(compute='_compute_dental_chart_data')

    @api.depends('chart_line_ids.condition', 'chart_line_ids.date', 'chart_line_ids.surfaces')
    def _compute_dental_chart_data(self):
        """{tooth_code: {condition, label, surfaces, date}} with the latest entry per tooth."""
        labels = dict(self.env['dental.chart.line']._fields['condition']._description_selection(self.env))
        for partner in self:
            data = {}
            for line in partner.chart_line_ids.sorted(lambda l: (l.date or fields.Date.today(), l.id)):
                data[line.tooth_id.code] = {
                    'condition': line.condition,
                    'label': labels.get(line.condition, ''),
                    'surfaces': line.surfaces or '',
                    'date': fields.Date.to_string(line.date) if line.date else '',
                }
            partner.dental_chart_data = data


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    is_dental = fields.Boolean(compute='_compute_is_dental')

    @api.depends('department_id')
    def _compute_is_dental(self):
        dentistry = self.env.ref('dental_core.dept_dentistry', raise_if_not_found=False)
        for visit in self:
            visit.is_dental = bool(dentistry) and visit.department_id == dentistry

    def _brief_specialty_facts(self):
        facts = super()._brief_specialty_facts()
        if self.is_dental:
            notable = ('caries', 'fractured', 'rct', 'implant', 'missing', 'extracted', 'crown', 'bridge')
            chart = self.patient_id.dental_chart_data or {}
            rows = [f"{code}: {info.get('label')}{' ' + info['surfaces'] if info.get('surfaces') else ''}"
                    for code, info in sorted(chart.items()) if info.get('condition') in notable]
            facts.append((_("Tooth chart"), rows))
        return facts
