from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command


class MedicalClaim(models.Model):
    _inherit = 'medical.claim'

    move_id = fields.Many2one('account.move', string='Insurer Invoice', readonly=True, copy=False)
    refund_move_ids = fields.One2many('account.move', 'medical_claim_id', string='Related Entries',
                                      domain=[('medical_move_kind', '=', 'insurer_refund')])
    payment_state = fields.Selection(related='move_id.payment_state', string='Payment Status')
    amount_residual = fields.Monetary(related='move_id.amount_residual', string='Open Balance')

    def _create_insurer_invoice(self):
        for claim in self:
            company = claim.company_id
            company._medical_check_config('medical_insurance_journal_id', 'medical_insurance_receivable_account_id')
            claim.insurer_id._medical_set_insurer_receivable()
            line_vals = [Command.create(cl.treatment_line_id._prepare_insurer_invoice_line_vals(cl))
                         for cl in claim.line_ids]
            move = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'journal_id': company.medical_insurance_journal_id.id,
                'partner_id': claim.insurer_id.id,
                # with month-end accruals the revenue is already in the service month:
                # the invoice is dated when issued so the accrual reversal offsets it
                'invoice_date': fields.Date.context_today(self) if company.medical_insurance_accrual else claim.date_to,
                'invoice_origin': claim.name,
                'ref': claim.name,
                'medical_claim_id': claim.id,
                'medical_move_kind': 'insurer',
                'company_id': company.id,
                'invoice_line_ids': line_vals,
            })
            move.action_post()
            claim.move_id = move

    def action_submit(self):
        res = super().action_submit()
        self.filtered(lambda c: c.company_id._medical_accounting_ready())._create_insurer_invoice()
        return res

    def _create_rejection_credit_note(self):
        """Credit the rejected part of each line so the insurer balance equals the approved amount."""
        for claim in self:
            rejected = claim.line_ids.filtered(lambda l: l.currency_id.compare_amounts(l.rejected_amount, 0) > 0)
            if not rejected or not claim.move_id:
                continue
            vals_list = []
            for cl in rejected:
                vals = cl.treatment_line_id._prepare_insurer_invoice_line_vals(cl)
                vals.update({
                    'price_unit': cl.rejected_amount,
                    'name': f"{vals['name']} | {_('rejected')}: {cl.reject_reason or '-'}",
                })
                vals_list.append(Command.create(vals))
            refund = self.env['account.move'].create({
                'move_type': 'out_refund',
                'journal_id': claim.move_id.journal_id.id,
                'partner_id': claim.insurer_id.id,
                'invoice_date': claim.settlement_date or fields.Date.context_today(self),
                'reversed_entry_id': claim.move_id.id,
                'ref': _("Rejections %s", claim.name),
                'medical_claim_id': claim.id,
                'medical_move_kind': 'insurer_refund',
                'company_id': claim.company_id.id,
                'invoice_line_ids': vals_list,
            })
            refund.action_post()
            (claim.move_id + refund).line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled).reconcile()

    def action_settle(self):
        res = super().action_settle()
        self._create_rejection_credit_note()
        return res

    def action_view_invoice(self):
        self.ensure_one()
        moves = self.move_id | self.refund_move_ids
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_out_invoice_type')
        action['domain'] = [('id', 'in', moves.ids)]
        action['context'] = {'create': False}
        if len(moves) == 1:
            action.update({'view_mode': 'form', 'views': [(False, 'form')], 'res_id': moves.id})
        return action

    # --------------------------------------------------------------- accruals
    @api.model
    def _cron_insurance_accrual(self):
        today = fields.Date.context_today(self)
        period_end = today.replace(day=1) - relativedelta(days=1)
        for company in self.env['res.company'].search([('medical_insurance_accrual', '=', True)]):
            self.with_company(company)._post_insurance_accrual(company, period_end)

    @api.model
    def _post_insurance_accrual(self, company, period_end):
        """Accrue insurer shares served up to period_end but not yet invoiced; auto-reversed next day."""
        company._medical_check_config('medical_unbilled_insurance_account_id', 'medical_deferred_journal_id')
        ref = _("Insurance accrual %s", period_end.strftime('%m/%Y'))
        Move = self.env['account.move']
        if Move.search_count([('medical_move_kind', '=', 'accrual'), ('company_id', '=', company.id),
                              ('date', '=', period_end), ('reversed_entry_id', '=', False)], limit=1):
            return Move
        lines = self.env['medical.claim.line'].search([
            ('company_id', '=', company.id),
            ('state', '=', 'to_claim'),
            ('service_date', '<=', period_end),
        ])
        if not lines:
            return Move
        move_lines = []
        total = 0.0
        for cl in lines:
            tl = cl.treatment_line_id
            income = tl.product_id._get_product_accounts()['income']
            vals = {
                'account_id': income.id,
                'name': f"{cl.insurer_id.name} - {cl.patient_id.name} - {tl.product_id.name}",
                'credit': cl.claimed,
                'partner_id': cl.insurer_id.id,
                'medical_claim_line_id': cl.id,
            }
            distribution = tl._medical_analytic_distribution()
            if distribution:
                vals['analytic_distribution'] = distribution
            move_lines.append(Command.create(vals))
            total += cl.claimed
        move_lines.append(Command.create({
            'account_id': company.medical_unbilled_insurance_account_id.id,
            'name': ref,
            'debit': total,
        }))
        move = Move.create({
            'move_type': 'entry',
            'journal_id': company.medical_deferred_journal_id.id,
            'date': period_end,
            'ref': ref,
            'medical_move_kind': 'accrual',
            'company_id': company.id,
            'line_ids': move_lines,
        })
        move.action_post()
        reversal = move._reverse_moves([{'date': period_end + relativedelta(days=1),
                                          'ref': _("Reversal of %s", ref),
                                          'medical_move_kind': 'accrual'}])
        reversal.action_post()
        return move


class MedicalClaimLine(models.Model):
    _inherit = 'medical.claim.line'

    rebill_move_id = fields.Many2one('account.move', string='Re-bill Invoice', readonly=True, copy=False)
    writeoff_move_id = fields.Many2one('account.move', string='Write-off Entry', readonly=True, copy=False)

    def _check_rejection_decision(self):
        for line in self:
            if line.state not in ('rejected', 'partially_approved') or \
                    line.currency_id.compare_amounts(line.rejected_amount, 0) <= 0:
                raise UserError(_("%s has no open rejected amount.", line.display_name))

    def action_rebill_patient(self):
        self._check_rejection_decision()
        for line in self:
            tl = line.treatment_line_id
            company = line.company_id
            company._medical_check_config('medical_patient_journal_id')
            vals = tl._prepare_patient_invoice_line_vals(
                amount=line.rejected_amount,
                label=_("%(proc)s - not covered by %(insurer)s (%(reason)s)", proc=tl.display_name,
                        insurer=line.insurer_id.name, reason=line.reject_reason or '-'))
            vals['medical_claim_line_id'] = line.id
            move = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'journal_id': company.medical_patient_journal_id.id,
                'partner_id': line.patient_id.id,
                'invoice_date': fields.Date.context_today(self),
                'invoice_origin': line.claim_id.name,
                'medical_visit_id': tl.visit_id.id,
                'medical_move_kind': 'rebill',
                'company_id': company.id,
                'invoice_line_ids': [Command.create(vals)],
            })
            move.action_post()
            line.write({'rebill_move_id': move.id, 'state': 'rebilled'})

    def action_write_off(self):
        self._check_rejection_decision()
        for line in self:
            company = line.company_id
            if company.medical_writeoff_to_loss:
                company._medical_check_config('medical_insurance_loss_account_id', 'medical_deferred_journal_id')
                tl = line.treatment_line_id
                income = tl.product_id._get_product_accounts()['income']
                distribution = tl._medical_analytic_distribution()
                move = self.env['account.move'].create({
                    'move_type': 'entry',
                    'journal_id': company.medical_deferred_journal_id.id,
                    'date': fields.Date.context_today(self),
                    'ref': _("Write-off of rejected claim - %s", line.display_name),
                    'medical_move_kind': 'writeoff',
                    'company_id': company.id,
                    'line_ids': [
                        Command.create({
                            'account_id': company.medical_insurance_loss_account_id.id,
                            'name': line.reject_reason or _("Insurance rejection"),
                            'debit': line.rejected_amount,
                            'partner_id': line.insurer_id.id,
                            'analytic_distribution': distribution or False,
                            'medical_claim_line_id': line.id,
                        }),
                        Command.create({
                            'account_id': income.id,
                            'name': line.display_name,
                            'credit': line.rejected_amount,
                            'partner_id': line.insurer_id.id,
                            'analytic_distribution': distribution or False,
                            'medical_claim_line_id': line.id,
                        }),
                    ],
                })
                move.action_post()
                line.writeoff_move_id = move
            line.state = 'written_off'
