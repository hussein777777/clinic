from odoo import _, fields, models
from odoo.fields import Command


class MedicalTreatmentLine(models.Model):
    _inherit = 'medical.treatment.line'

    invoice_line_ids = fields.One2many('account.move.line', 'medical_line_id', string='Invoice Lines')

    def _after_execute(self):
        res = super()._after_execute()
        for line in self.filtered(lambda l: l.care_contract_id.billing_mode == 'sessions'):
            line.care_contract_id._recognize_session(line)
        return res

    def _patient_invoice_lines(self, include_refunds=False):
        kinds = ('patient',)
        return self.invoice_line_ids.filtered(
            lambda l: l.move_id.medical_move_kind in kinds
            and (include_refunds or l.move_id.move_type == 'out_invoice'))

    def _medical_analytic_distribution(self):
        self.ensure_one()
        doctor = self.doctor_id
        account = doctor._medical_analytic_account() if doctor else False
        return {str(account.id): 100.0} if account else False

    def _invoice_label(self):
        self.ensure_one()
        label = self.display_name
        if self.insurance_amount and not self.insurance_excluded:
            label += ' - ' + _("insurer covers %(amount)s", amount=self.currency_id.format(self.insurance_amount))
        return label

    def _prepare_patient_invoice_line_vals(self, amount=None, label=None):
        self.ensure_one()
        vals = {
            'product_id': self.product_id.id,
            'name': label or self._invoice_label(),
            'quantity': 1,
            'price_unit': self.patient_amount if amount is None else amount,
            'medical_line_id': self.id,
            'medical_doctor_id': self.doctor_id.id,
        }
        distribution = self._medical_analytic_distribution()
        if distribution:
            vals['analytic_distribution'] = distribution
        return vals

    def _prepare_insurer_invoice_line_vals(self, claim_line):
        self.ensure_one()
        parts = [
            self.patient_id.name or '',
            claim_line.member_no or '',
            self.product_id.insurance_code or self.product_id.name,
        ]
        if self.site_label:
            parts.append(self.site_label)
        if claim_line.approval_no:
            parts.append(_("approval %s", claim_line.approval_no))
        vals = {
            'product_id': self.product_id.id,
            'name': ' | '.join(p for p in parts if p),
            'quantity': 1,
            'price_unit': claim_line.claimed,
            'tax_ids': [Command.clear()],
            'medical_line_id': self.id,
            'medical_claim_line_id': claim_line.id,
            'medical_doctor_id': self.doctor_id.id,
        }
        distribution = self._medical_analytic_distribution()
        if distribution:
            vals['analytic_distribution'] = distribution
        return vals
