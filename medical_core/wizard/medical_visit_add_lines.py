from odoo import fields, models


class MedicalVisitAddLines(models.TransientModel):
    _name = 'medical.visit.add.lines'
    _description = 'Add planned treatments to a visit'

    visit_id = fields.Many2one('medical.visit', required=True)
    patient_id = fields.Many2one(related='visit_id.patient_id')
    line_ids = fields.Many2many(
        'medical.treatment.line', string='Planned Treatments',
        domain="[('patient_id', '=', patient_id), ('state', '=', 'planned'),"
               " ('plan_id.state', 'in', ('accepted', 'in_progress'))]")

    def action_confirm(self):
        self.line_ids.write({'visit_id': self.visit_id.id})
        if not self.visit_id.plan_id and self.line_ids.plan_id:
            self.visit_id.plan_id = self.line_ids.plan_id[:1]
        return {'type': 'ir.actions.act_window_close'}
