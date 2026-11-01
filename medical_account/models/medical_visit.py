from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    patient_move_id = fields.Many2one('account.move', string='Patient Invoice', readonly=True, copy=False)
    payment_state = fields.Selection(related='patient_move_id.payment_state', string='Payment Status')
    amount_due = fields.Monetary(related='patient_move_id.amount_residual', string='Amount Due')

    def _get_invoice_date(self):
        self.ensure_one()
        return fields.Date.context_today(self, self.start) if self.start else fields.Date.context_today(self)

    def _prepare_patient_invoice_vals(self):
        self.ensure_one()
        company = self.company_id
        lines = self.line_ids.filtered(lambda l: l.state == 'executed' and not l._patient_invoice_lines()
                                       and not l.currency_id.is_zero(l.patient_amount))
        if not lines:
            return False
        line_vals = [(0, 0, l._prepare_patient_invoice_line_vals()) for l in lines]
        total = sum(lines.mapped('patient_amount'))
        plans = (self.plan_id | lines.plan_id).filtered(lambda p: p.advance_balance > 0)
        for plan in plans:
            deduction = plan._advance_deduction_vals(total)
            if deduction:
                line_vals += deduction
                total -= deduction[0][2]['price_unit']
        return {
            'move_type': 'out_invoice',
            'journal_id': company.medical_patient_journal_id.id,
            'partner_id': self.patient_id.id,
            'invoice_date': self._get_invoice_date(),
            'invoice_origin': self.name,
            'medical_visit_id': self.id,
            'medical_move_kind': 'patient',
            'company_id': company.id,
            'invoice_line_ids': line_vals,
        }

    def _create_patient_invoice(self):
        for visit in self:
            visit.company_id._medical_check_config('medical_patient_journal_id', 'medical_advance_account_id')
            vals = visit._prepare_patient_invoice_vals()
            if not vals:
                continue
            move = self.env['account.move'].sudo().create(vals)
            move.action_post()
            visit.patient_move_id = move

    def action_done(self):
        res = super().action_done()
        ready = self.filtered(lambda v: v.company_id._medical_accounting_ready())
        ready._create_patient_invoice()
        ready.write({'state': 'invoiced'})
        return res

    def action_register_payment(self):
        self.ensure_one()
        if not self.patient_move_id:
            raise UserError(_("Nothing to collect on this visit."))
        return self.patient_move_id.action_register_payment()

    def action_view_patient_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.patient_move_id.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_reverse_invoice(self):
        """Manager correction: credit the patient invoice and drop insurer lines not yet claimed."""
        for visit in self:
            if visit.state != 'invoiced':
                raise UserError(_("Only invoiced visits can be reversed."))
            claimed = visit.line_ids.claim_line_ids.filtered(lambda c: c.state != 'to_claim')
            if claimed:
                raise UserError(_("Some lines were already sent to the insurer (%s). Settle them through the claim.",
                                  ", ".join(claimed.claim_id.mapped('name'))))
            visit.line_ids.claim_line_ids.sudo().unlink()
            if visit.patient_move_id and visit.patient_move_id.state == 'posted':
                visit.patient_move_id._reverse_moves(
                    [{'ref': _("Reversal of %s", visit.name), 'invoice_date': fields.Date.context_today(self),
                      'medical_move_kind': 'patient', 'medical_visit_id': visit.id}],
                    cancel=True)
            visit.line_ids._patient_invoice_lines(include_refunds=True).write({'medical_line_id': False})
            visit.write({'state': 'done', 'patient_move_id': False})

    def action_reinvoice(self):
        for visit in self.filtered(lambda v: v.state == 'done'):
            visit._create_claim_lines()
            visit._create_patient_invoice()
            visit.state = 'invoiced'
