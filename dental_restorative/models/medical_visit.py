from odoo import fields, models


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    post_op_instructions = fields.Html(string='Post-operative Instructions')

    def action_print_post_op(self):
        return self.env.ref('dental_restorative.action_report_dental_post_op').report_action(self)
