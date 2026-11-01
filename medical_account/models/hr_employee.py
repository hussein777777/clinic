from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    medical_analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Doctor Analytic Account', copy=False,
        groups='hr.group_hr_user,medical_core.group_medical_user',
        help='Revenue, lab costs and commissions of this doctor are tagged with this analytic account.')

    def _medical_ensure_analytic_account(self):
        for employee in self.sudo().filtered(lambda e: e.is_practitioner and not e.medical_analytic_account_id):
            company = employee.company_id or self.env.company
            plan = company.medical_analytic_plan_id
            if not plan:
                continue
            employee.medical_analytic_account_id = self.env['account.analytic.account'].sudo().create({
                'name': employee.name,
                'plan_id': plan.id,
                'company_id': company.id,
            })

    def _medical_analytic_account(self):
        self.ensure_one()
        employee = self.sudo()
        if not employee.medical_analytic_account_id:
            employee._medical_ensure_analytic_account()
        return employee.medical_analytic_account_id

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        employees._medical_ensure_analytic_account()
        return employees

    def write(self, vals):
        res = super().write(vals)
        if vals.get('is_practitioner'):
            self._medical_ensure_analytic_account()
        return res
