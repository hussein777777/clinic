from odoo import fields, models, _
from odoo.exceptions import UserError

# key: (field, name, account_type, start code)
MEDICAL_ACCOUNTS = [
    ('medical_advance_account_id', 'Patient Advances', 'liability_current', '2100'),
    ('medical_deferred_account_id', 'Deferred Revenue - Care Programs', 'liability_current', '2100'),
    ('medical_insurance_receivable_account_id', 'Insurance Companies Receivable', 'asset_receivable', '1200'),
    ('medical_unbilled_insurance_account_id', 'Unbilled Insurance Revenue', 'asset_current', '1200'),
    ('medical_program_revenue_account_id', 'Revenue - Care Programs', 'income', '4000'),
    ('medical_insurance_loss_account_id', 'Insurance Rejection Losses', 'expense', '6000'),
    ('medical_commission_expense_account_id', 'Doctor Commissions', 'expense', '6000'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    medical_patient_journal_id = fields.Many2one('account.journal', string='Patient Invoices Journal',
                                                domain="[('type', '=', 'sale'), ('company_id', '=', id)]")
    medical_insurance_journal_id = fields.Many2one('account.journal', string='Insurer Invoices Journal',
                                                  domain="[('type', '=', 'sale'), ('company_id', '=', id)]")
    medical_deferred_journal_id = fields.Many2one('account.journal', string='Deferred Revenue Journal',
                                                 domain="[('type', '=', 'general'), ('company_id', '=', id)]")
    medical_commission_journal_id = fields.Many2one('account.journal', string='Commission Bills Journal',
                                                   domain="[('type', '=', 'purchase'), ('company_id', '=', id)]")
    medical_advance_account_id = fields.Many2one('account.account', string='Patient Advances Account')
    medical_deferred_account_id = fields.Many2one('account.account', string='Deferred Program Revenue Account')
    medical_program_revenue_account_id = fields.Many2one('account.account', string='Program Revenue Account')
    medical_insurance_receivable_account_id = fields.Many2one('account.account', string='Insurer Receivable Account')
    medical_unbilled_insurance_account_id = fields.Many2one('account.account', string='Unbilled Insurance Account')
    medical_insurance_loss_account_id = fields.Many2one('account.account', string='Insurance Rejection Loss Account')
    medical_commission_expense_account_id = fields.Many2one('account.account', string='Commission Expense Account')
    medical_insurance_accrual = fields.Boolean(string='Accrue Unbilled Insurance Revenue',
                                              help='Post a month-end accrual for insurer shares not yet invoiced '
                                                   '(reversed on the 1st of the next month).')
    medical_writeoff_to_loss = fields.Boolean(string='Book Rejections as a Loss', default=True,
                                             help='Written-off rejections are moved from revenue to the loss account '
                                                  'so they stay visible in the P&L.')
    medical_commission_basis = fields.Selection([('collected', 'Collected'), ('invoiced', 'Invoiced')],
                                               default='collected', string='Commission Basis')
    medical_analytic_plan_id = fields.Many2one('account.analytic.plan', string='Doctors Analytic Plan')

    def _medical_create_journal(self, name, code, jtype):
        self.ensure_one()
        Journal = self.env['account.journal'].with_company(self)
        journal = Journal.search([('code', '=', code), ('company_id', '=', self.id)], limit=1)
        return journal or Journal.create({'name': name, 'code': code, 'type': jtype, 'company_id': self.id})

    def _medical_setup_accounting(self):
        """Create (only what is missing) the journals, accounts and analytic plan used by the medical modules."""
        for company in self:
            if not company.chart_template and not self.env['account.account'].with_company(company).search_count(
                    [('company_ids', 'in', company.id)], limit=1):
                raise UserError(_("Install a chart of accounts for %s first.", company.name))
            Account = self.env['account.account'].with_company(company)
            vals = {}
            for field, name, account_type, start in MEDICAL_ACCOUNTS:
                if company[field]:
                    continue
                account = Account.search([('name', '=', name), ('company_ids', 'in', company.id)], limit=1)
                if not account:
                    account = Account.create({
                        'name': name,
                        'code': Account._search_new_account_code(start),
                        'account_type': account_type,
                        'reconcile': account_type in ('asset_receivable', 'liability_current'),
                        'company_ids': [(6, 0, company.ids)],
                    })
                vals[field] = account.id
            if not company.medical_patient_journal_id:
                vals['medical_patient_journal_id'] = company._medical_create_journal(_('Patient Invoices'), 'PINV', 'sale').id
            if not company.medical_insurance_journal_id:
                vals['medical_insurance_journal_id'] = company._medical_create_journal(_('Insurance Invoices'), 'INSV', 'sale').id
            if not company.medical_deferred_journal_id:
                vals['medical_deferred_journal_id'] = company._medical_create_journal(_('Medical Deferrals'), 'DEFR', 'general').id
            if not company.medical_commission_journal_id:
                vals['medical_commission_journal_id'] = company._medical_create_journal(_('Doctor Commissions'), 'DCOM', 'purchase').id
            if not company.medical_analytic_plan_id:
                plan = self.env['account.analytic.plan'].search([('name', '=', 'Doctors')], limit=1) \
                    or self.env['account.analytic.plan'].create({'name': 'Doctors', 'default_applicability': 'optional'})
                vals['medical_analytic_plan_id'] = plan.id
            company.write(vals)
            company._medical_apply_account_properties()

    def _medical_apply_account_properties(self):
        """Route the advance product to its balance-sheet account and insurers to their receivable."""
        for company in self:
            advance = self.env.ref('medical_account.product_patient_advance', raise_if_not_found=False)
            if advance and company.medical_advance_account_id:
                advance.product_tmpl_id.with_company(company).property_account_income_id = company.medical_advance_account_id
            if company.medical_insurance_receivable_account_id:
                insurers = self.env['res.partner'].with_company(company).search([('is_insurer', '=', True)])
                insurers.property_account_receivable_id = company.medical_insurance_receivable_account_id

    def _medical_accounting_ready(self):
        """False while the company has no chart of accounts (fresh database): clinical flows still work."""
        self.ensure_one()
        return bool(self.chart_template or self.env['account.account'].with_company(self).search_count(
            [('company_ids', 'in', self.id)], limit=1))

    def _medical_check_config(self, *fields_needed):
        self.ensure_one()
        if any(not self[f] for f in fields_needed):
            self._medical_setup_accounting()
