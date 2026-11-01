from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command


class MedicalTreatmentPlan(models.Model):
    _inherit = 'medical.treatment.plan'

    advance_move_ids = fields.One2many('account.move.line', 'medical_plan_id', string='Advance Journal Items')
    advance_balance = fields.Monetary(compute='_compute_advance_balance',
                                      help='Advances received for this plan and not yet deducted from an invoice.')
    advance_invoice_count = fields.Integer(compute='_compute_advance_balance')

    @api.depends('advance_move_ids.balance', 'advance_move_ids.parent_state')
    def _compute_advance_balance(self):
        for plan in self:
            account = plan.company_id.medical_advance_account_id
            lines = plan.advance_move_ids.filtered(
                lambda l: l.parent_state == 'posted' and l.account_id == account)
            plan.advance_balance = -sum(lines.mapped('balance'))
            plan.advance_invoice_count = len(lines.move_id.filtered(lambda m: m.medical_move_kind == 'advance'))

    def _create_advance_invoice(self, amount):
        self.ensure_one()
        if amount <= 0:
            raise UserError(_("The advance must be positive."))
        company = self.company_id
        company._medical_check_config('medical_advance_account_id', 'medical_patient_journal_id')
        product = self.env.ref('medical_account.product_patient_advance')
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'journal_id': company.medical_patient_journal_id.id,
            'partner_id': self.patient_id.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_origin': self.name,
            'medical_move_kind': 'advance',
            'company_id': company.id,
            'invoice_line_ids': [Command.create({
                'product_id': product.id,
                'name': _("Advance - treatment plan %s", self.name),
                'quantity': 1,
                'price_unit': amount,
                'tax_ids': [Command.clear()],
                'account_id': company.medical_advance_account_id.id,
                'medical_plan_id': self.id,
            })],
        })
        move.action_post()
        return move

    def _advance_deduction_vals(self, invoice_total):
        """Negative line consuming the plan advance on a patient invoice."""
        self.ensure_one()
        deduct = min(self.advance_balance, invoice_total)
        if self.currency_id.compare_amounts(deduct, 0.0) <= 0:
            return []
        product = self.env.ref('medical_account.product_patient_advance')
        return [Command.create({
            'product_id': product.id,
            'name': _("Advance deduction - %s", self.name),
            'quantity': -1,
            'price_unit': deduct,
            'tax_ids': [Command.clear()],
            'account_id': self.company_id.medical_advance_account_id.id,
            'medical_plan_id': self.id,
        })]

    def action_open_advance_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Receive Advance'),
            'res_model': 'medical.advance.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_plan_id': self.id, 'default_amount': self.amount_patient_est},
        }

    def action_view_advance_invoices(self):
        self.ensure_one()
        moves = self.advance_move_ids.move_id.filtered(lambda m: m.medical_move_kind == 'advance')
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_out_invoice_type')
        action['domain'] = [('id', 'in', moves.ids)]
        action['context'] = {'create': False}
        return action
