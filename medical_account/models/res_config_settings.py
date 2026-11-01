from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    medical_patient_journal_id = fields.Many2one(related='company_id.medical_patient_journal_id', readonly=False)
    medical_insurance_journal_id = fields.Many2one(related='company_id.medical_insurance_journal_id', readonly=False)
    medical_deferred_journal_id = fields.Many2one(related='company_id.medical_deferred_journal_id', readonly=False)
    medical_commission_journal_id = fields.Many2one(related='company_id.medical_commission_journal_id', readonly=False)
    medical_advance_account_id = fields.Many2one(related='company_id.medical_advance_account_id', readonly=False)
    medical_deferred_account_id = fields.Many2one(related='company_id.medical_deferred_account_id', readonly=False)
    medical_program_revenue_account_id = fields.Many2one(related='company_id.medical_program_revenue_account_id', readonly=False)
    medical_insurance_receivable_account_id = fields.Many2one(related='company_id.medical_insurance_receivable_account_id', readonly=False)
    medical_unbilled_insurance_account_id = fields.Many2one(related='company_id.medical_unbilled_insurance_account_id', readonly=False)
    medical_insurance_loss_account_id = fields.Many2one(related='company_id.medical_insurance_loss_account_id', readonly=False)
    medical_commission_expense_account_id = fields.Many2one(related='company_id.medical_commission_expense_account_id', readonly=False)
    medical_insurance_accrual = fields.Boolean(related='company_id.medical_insurance_accrual', readonly=False)
    medical_writeoff_to_loss = fields.Boolean(related='company_id.medical_writeoff_to_loss', readonly=False)
    medical_commission_basis = fields.Selection(related='company_id.medical_commission_basis', readonly=False)

    def action_medical_setup_accounting(self):
        self.company_id._medical_setup_accounting()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def set_values(self):
        super().set_values()
        self.company_id._medical_apply_account_properties()
