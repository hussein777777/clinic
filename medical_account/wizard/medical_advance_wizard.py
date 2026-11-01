from odoo import fields, models


class MedicalAdvanceWizard(models.TransientModel):
    _name = 'medical.advance.wizard'
    _description = 'Receive a patient advance'

    plan_id = fields.Many2one('medical.treatment.plan', required=True)
    currency_id = fields.Many2one(related='plan_id.currency_id')
    amount = fields.Monetary(required=True)

    def action_confirm(self):
        move = self.plan_id._create_advance_invoice(self.amount)
        return move.action_register_payment()
