from odoo import api, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _medical_set_insurer_receivable(self):
        company = self.env.company
        account = company.medical_insurance_receivable_account_id
        if account:
            self.filtered('is_insurer').with_company(company).property_account_receivable_id = account

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        partners.filtered('is_insurer')._medical_set_insurer_receivable()
        return partners

    def write(self, vals):
        res = super().write(vals)
        if vals.get('is_insurer'):
            self._medical_set_insurer_receivable()
        return res
