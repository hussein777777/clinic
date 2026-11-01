from odoo import api, fields, models


class MedicalReconciliationCheck(models.TransientModel):
    _name = 'medical.reconciliation.check'
    _description = 'Medical sub-ledger reconciliation check'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today)

    deferred_subledger = fields.Monetary(compute='_compute_check', string='Programs: unearned')
    deferred_gl = fields.Monetary(compute='_compute_check', string='GL: deferred program revenue')
    deferred_diff = fields.Monetary(compute='_compute_check')
    advance_subledger = fields.Monetary(compute='_compute_check', string='Plans: unused advances')
    advance_gl = fields.Monetary(compute='_compute_check', string='GL: patient advances')
    advance_diff = fields.Monetary(compute='_compute_check')
    insurance_subledger = fields.Monetary(compute='_compute_check', string='Claims: open insurer balance')
    insurance_gl = fields.Monetary(compute='_compute_check', string='GL: insurer receivable')
    insurance_diff = fields.Monetary(compute='_compute_check')
    unclaimed_amount = fields.Monetary(compute='_compute_check', string='Served, not yet claimed')

    def _gl_balance(self, account):
        if not account:
            return 0.0
        lines = self.env['account.move.line'].search([
            ('account_id', '=', account.id), ('parent_state', '=', 'posted'),
            ('date', '<=', self.date), ('company_id', '=', self.company_id.id)])
        return sum(lines.mapped('balance'))

    @api.depends('company_id', 'date')
    def _compute_check(self):
        for check in self:
            company = check.company_id
            Case = self.env['medical.care.contract']
            cases = Case.search([('company_id', '=', company.id), ('contract_move_id', '!=', False)])
            check.deferred_subledger = sum(cases.mapped('deferred_balance'))
            check.deferred_gl = -check._gl_balance(company.medical_deferred_account_id)
            check.deferred_diff = check.deferred_gl - check.deferred_subledger

            plans = self.env['medical.treatment.plan'].search([('company_id', '=', company.id),
                                                              ('advance_move_ids', '!=', False)])
            check.advance_subledger = sum(plans.mapped('advance_balance'))
            check.advance_gl = -check._gl_balance(company.medical_advance_account_id)
            check.advance_diff = check.advance_gl - check.advance_subledger

            claims = self.env['medical.claim'].search([('company_id', '=', company.id), ('move_id', '!=', False)])
            check.insurance_subledger = sum(claims.mapped('amount_residual'))
            check.insurance_gl = check._gl_balance(company.medical_insurance_receivable_account_id)
            check.insurance_diff = check.insurance_gl - check.insurance_subledger

            unclaimed = self.env['medical.claim.line'].search([('company_id', '=', company.id), ('state', '=', 'to_claim')])
            check.unclaimed_amount = sum(unclaimed.mapped('claimed'))
