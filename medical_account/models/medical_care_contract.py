from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.fields import Command


class MedicalCareContract(models.Model):
    _inherit = 'medical.care.contract'

    contract_move_id = fields.Many2one('account.move', string='Contract Invoice', readonly=True, copy=False)
    payment_term_id = fields.Many2one('account.payment.term', readonly=True, copy=False)
    move_ids = fields.One2many('account.move', 'care_contract_id', string='Accounting Entries')
    recognized_months = fields.Integer(readonly=True, copy=False)
    last_recognized_date = fields.Date(readonly=True, copy=False)
    recognized_amount = fields.Monetary(compute='_compute_financials')
    deferred_balance = fields.Monetary(compute='_compute_financials', help='Invoiced but not yet earned.')
    amount_paid = fields.Monetary(compute='_compute_financials')
    overdue_amount = fields.Monetary(compute='_compute_financials')
    is_late = fields.Boolean(compute='_compute_financials', search='_search_is_late')

    def _deferred_account(self):
        self.ensure_one()
        return self.company_id.medical_deferred_account_id

    def _revenue_account(self):
        """Program revenue: the package's own income account when set, else the company default."""
        self.ensure_one()
        own = self.product_id.product_tmpl_id.with_company(self.company_id).property_account_income_id
        return own if own and own != self._deferred_account() else self.company_id.medical_program_revenue_account_id

    def _grace_days(self):
        return int(self.env['ir.config_parameter'].sudo().get_param('medical_account.program_late_grace_days', 7))

    @api.depends('move_ids.state', 'contract_move_id.amount_residual')
    def _compute_financials(self):
        today = fields.Date.context_today(self)
        for contract in self:
            account = contract._deferred_account()
            posted = contract.move_ids.filtered(lambda m: m.state == 'posted')
            rec_lines = posted.filtered(lambda m: m.medical_move_kind == 'program_recognition').line_ids.filtered(
                lambda l: l.account_id == account)
            contract.recognized_amount = sum(rec_lines.mapped('debit')) - sum(rec_lines.mapped('credit'))
            contract.deferred_balance = -sum(posted.line_ids.filtered(lambda l: l.account_id == account).mapped('balance'))
            inv = contract.contract_move_id
            if inv and inv.state == 'posted':
                contract.amount_paid = inv.amount_total - inv.amount_residual
                receivables = inv.line_ids.filtered(
                    lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
                contract.overdue_amount = sum(
                    receivables.filtered(lambda l: l.date_maturity and l.date_maturity < today).mapped('amount_residual'))
            else:
                contract.amount_paid = 0.0
                contract.overdue_amount = 0.0
            grace = contract._grace_days()
            contract.is_late = bool(inv) and any(
                l.date_maturity and l.date_maturity < today - relativedelta(days=grace) and l.amount_residual > 0
                for l in inv.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable'))

    def _search_is_late(self, operator, value):
        if operator in ('in', 'not in'):
            value = True in value
            operator = '=' if operator == 'in' else '!='
        if operator not in ('=', '!=') or not isinstance(value, bool):
            return NotImplemented
        limit = fields.Date.context_today(self) - relativedelta(days=self._grace_days())
        lines = self.env['account.move.line'].sudo().search([
            ('move_id.medical_move_kind', '=', 'program_contract'),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('reconciled', '=', False),
            ('date_maturity', '<', limit),
        ])
        ids = lines.move_id.care_contract_id.ids
        return [('id', 'in' if (operator == '=') == value else 'not in', ids)]

    # ---------------------------------------------------------------- contract
    def _prepare_payment_term(self):
        self.ensure_one()
        lines = []
        rest = self.package_price - self.down_payment
        n = self.installment_count
        if self.down_payment and n:
            lines.append(Command.create({'value': 'fixed', 'value_amount': self.down_payment,
                                         'delay_type': 'days_after', 'nb_days': 0}))
        if n:
            per = self.currency_id.round(rest / n)
            for i in range(1, n):
                lines.append(Command.create({'value': 'fixed', 'value_amount': per,
                                             'delay_type': 'days_after', 'nb_days': 30 * i}))
            # Odoo: the last line always takes the balance and must be the 100% percent line
            lines.append(Command.create({'value': 'percent', 'value_amount': 100.0,
                                         'delay_type': 'days_after', 'nb_days': 30 * n}))
        else:
            lines.append(Command.create({'value': 'percent', 'value_amount': 100.0,
                                         'delay_type': 'days_after', 'nb_days': 0}))
        return {
            'name': _("Program %(contract)s - %(n)s installments", contract=self.name, n=n),
            'company_id': self.company_id.id,
            'line_ids': lines,
            'active': False,
        }

    def _contract_line_label(self):
        self.ensure_one()
        if self.billing_mode == 'sessions':
            return _("%(product)s - %(n)s sessions (%(contract)s)", product=self.product_id.name,
                     n=self.session_count, contract=self.name)
        return _("%(product)s - %(months)s months (%(contract)s)", product=self.product_id.name,
                 months=self.est_months, contract=self.name)

    def _create_contract_invoice(self):
        for contract in self:
            company = contract.company_id
            company._medical_check_config('medical_deferred_account_id', 'medical_program_revenue_account_id',
                                          'medical_patient_journal_id', 'medical_deferred_journal_id')
            term = self.env['account.payment.term'].create(contract._prepare_payment_term())
            doctor_account = contract.doctor_id._medical_analytic_account()
            move = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'journal_id': company.medical_patient_journal_id.id,
                'partner_id': contract.patient_id.id,
                'invoice_date': contract.start_date,
                'invoice_payment_term_id': term.id,
                'invoice_origin': contract.name,
                'care_contract_id': contract.id,
                'medical_move_kind': 'program_contract',
                'company_id': company.id,
                'invoice_line_ids': [Command.create({
                    'product_id': contract.product_id.id,
                    'name': contract._contract_line_label(),
                    'quantity': 1,
                    'price_unit': contract.package_price,
                    'account_id': contract._deferred_account().id,
                    'tax_ids': [Command.clear()],
                    'medical_doctor_id': contract.doctor_id.id,
                    'analytic_distribution': {str(doctor_account.id): 100.0} if doctor_account else False,
                })],
            })
            move.action_post()
            contract.write({'contract_move_id': move.id, 'payment_term_id': term.id})

    def action_start(self):
        res = super().action_start()
        self.filtered(lambda c: not c.contract_move_id and c.package_price
                      and c.company_id._medical_accounting_ready())._create_contract_invoice()
        return res

    # ------------------------------------------------------------- recognition
    def _post_recognition(self, amount, date, label, doctor=None):
        self.ensure_one()
        company = self.company_id
        doctor = doctor or self.doctor_id
        doctor_account = doctor._medical_analytic_account()
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': company.medical_deferred_journal_id.id,
            'date': date,
            'ref': label,
            'care_contract_id': self.id,
            'medical_move_kind': 'program_recognition',
            'company_id': company.id,
            'line_ids': [
                Command.create({
                    'account_id': self._deferred_account().id,
                    'name': label,
                    'debit': amount,
                    'partner_id': self.patient_id.id,
                }),
                Command.create({
                    'account_id': self._revenue_account().id,
                    'name': label,
                    'credit': amount,
                    'partner_id': self.patient_id.id,
                    'medical_doctor_id': doctor.id,
                    'analytic_distribution': {str(doctor_account.id): 100.0} if doctor_account else False,
                }),
            ],
        })
        move.action_post()
        return move

    def _recognize_until(self, period_end):
        """Monthly programs: post one recognition entry per elapsed month up to period_end (inclusive)."""
        for contract in self.filtered(lambda c: c.contract_move_id and c.billing_mode == 'monthly'
                                      and c.state in ('active', 'maintenance')):
            while contract.recognized_months < contract.est_months:
                month_end = fields.Date.end_of(contract.start_date + relativedelta(months=contract.recognized_months), 'month')
                if month_end > period_end:
                    break
                remaining = contract.package_price - contract.recognized_amount
                if contract.recognized_months == contract.est_months - 1:
                    amount = remaining
                else:
                    amount = min(contract.currency_id.round(contract.package_price / contract.est_months), remaining)
                if contract.currency_id.compare_amounts(amount, 0) > 0:
                    contract._post_recognition(amount, month_end, _("Program revenue %(contract)s - %(period)s",
                                                                    contract=contract.name,
                                                                    period=month_end.strftime('%m/%Y')))
                contract.write({'recognized_months': contract.recognized_months + 1, 'last_recognized_date': month_end})
                contract.invalidate_recordset(['recognized_amount', 'deferred_balance'])

    def _recognize_session(self, line):
        """Sessions programs: earn one share of the package each time a session is executed."""
        self.ensure_one()
        if not self.contract_move_id or self.billing_mode != 'sessions':
            return
        remaining = self.deferred_balance
        if self.currency_id.compare_amounts(remaining, 0) <= 0:
            return
        is_last = self.sessions_used >= self.session_count
        amount = remaining if is_last else min(self.currency_id.round(self.package_price / self.session_count), remaining)
        date = line.date_executed or fields.Date.context_today(self)
        self._post_recognition(amount, date, _("Session %(n)s/%(total)s - %(contract)s", n=self.sessions_used,
                                               total=self.session_count, contract=self.name),
                               doctor=line.doctor_id or self.doctor_id)
        self.write({'last_recognized_date': date})
        self.invalidate_recordset(['recognized_amount', 'deferred_balance'])

    @api.model
    def _cron_recognize_program_revenue(self):
        period_end = fields.Date.context_today(self).replace(day=1) - relativedelta(days=1)
        self.search([('state', 'in', ('active', 'maintenance')), ('billing_mode', '=', 'monthly'),
                     ('contract_move_id', '!=', False)])._recognize_until(period_end)

    def _recognize_all_remaining(self, date, label):
        for contract in self:
            remaining = contract.deferred_balance
            if contract.currency_id.compare_amounts(remaining, 0) > 0:
                contract._post_recognition(remaining, date, label)
            contract.write({'recognized_months': contract.est_months, 'last_recognized_date': date})

    def action_complete(self):
        today = fields.Date.context_today(self)
        for contract in self.filtered(lambda c: c.contract_move_id and c.state in ('active', 'maintenance')):
            contract._recognize_all_remaining(today, _("Program revenue %s - completed", contract.name))
        return super().action_complete()

    def action_terminate(self):
        today = fields.Date.context_today(self)
        for contract in self.filtered(lambda c: c.contract_move_id and c.state in ('active', 'maintenance')):
            # monthly: earn the months already served (current month included); sessions are earned as they happen
            contract._recognize_until(fields.Date.end_of(today, 'month'))
            remaining = contract.deferred_balance
            if contract.currency_id.compare_amounts(remaining, 0) > 0:
                refund = self.env['account.move'].create({
                    'move_type': 'out_refund',
                    'journal_id': contract.contract_move_id.journal_id.id,
                    'partner_id': contract.patient_id.id,
                    'invoice_date': today,
                    'reversed_entry_id': contract.contract_move_id.id,
                    'ref': _("Early termination %s", contract.name),
                    'care_contract_id': contract.id,
                    'medical_move_kind': 'program_termination',
                    'company_id': contract.company_id.id,
                    'invoice_line_ids': [Command.create({
                        'product_id': contract.product_id.id,
                        'name': _("Unused part of the program - %s", contract.name),
                        'quantity': 1,
                        'price_unit': remaining,
                        'account_id': contract._deferred_account().id,
                        'tax_ids': [Command.clear()],
                    })],
                })
                refund.action_post()
                (contract.contract_move_id + refund).line_ids.filtered(
                    lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled).reconcile()
        return super().action_terminate()

    def action_view_contract_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.contract_move_id.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_view_entries(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_journal_line')
        action['domain'] = [('care_contract_id', '=', self.id)]
        action['context'] = {'create': False}
        return action
