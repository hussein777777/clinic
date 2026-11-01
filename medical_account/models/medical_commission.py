from collections import defaultdict

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command



class MedicalCommissionRule(models.Model):
    _name = 'medical.commission.rule'
    _description = 'Doctor Commission Rule'
    _order = 'doctor_id, sequence, id'

    sequence = fields.Integer(default=10)
    doctor_id = fields.Many2one('hr.employee', string='Doctor', required=True, domain=[('is_practitioner', '=', True)],
                                index=True)
    department_id = fields.Many2one('medical.department', string='Clinic', help='Empty = all clinics.')
    specialty_id = fields.Many2one('medical.specialty', string='Specialty', help='Empty = all specialties.')
    categ_id = fields.Many2one('product.category', string='Category')
    rate = fields.Float(string='Rate (%)', required=True)
    deduct_lab = fields.Boolean(string='Deduct Lab Cost', default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    @api.constrains('rate')
    def _check_rate(self):
        for rule in self:
            if not 0 <= rule.rate <= 100:
                raise ValidationError(_("The commission rate must be between 0 and 100%."))

    @api.model
    def _match(self, doctor, product, company):
        rules = self.search([('doctor_id', '=', doctor.id), ('company_id', '=', company.id)], order='sequence, id')
        for rule in rules:
            if rule.categ_id and product.categ_id != rule.categ_id \
                    and not product.categ_id.parent_path.startswith(rule.categ_id.parent_path or '~'):
                continue
            if rule.specialty_id and product.specialty_id != rule.specialty_id:
                continue
            if rule.department_id and product.medical_department_id != rule.department_id:
                continue
            return rule
        return self.browse()


class MedicalCommissionLine(models.Model):
    _name = 'medical.commission.line'
    _description = 'Doctor Commission Line'
    _order = 'date desc, id desc'

    doctor_id = fields.Many2one('hr.employee', string='Doctor', required=True, index=True)
    date = fields.Date(required=True, help='Collection (or invoice) date.')
    date_from = fields.Date()
    date_to = fields.Date()
    move_line_id = fields.Many2one('account.move.line', string='Invoice Line', ondelete='set null')
    move_id = fields.Many2one(related='move_line_id.move_id', store=True, string='Invoice')
    partner_id = fields.Many2one(related='move_line_id.move_id.partner_id', string='Billed To')
    product_id = fields.Many2one('product.product', string='Procedure')
    rule_id = fields.Many2one('medical.commission.rule', ondelete='set null')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    base_amount = fields.Monetary(string='Collected', help='Part of the invoice line collected in the period.')
    lab_deduction = fields.Monetary()
    rate = fields.Float(string='Rate (%)')
    amount = fields.Monetary(string='Commission')
    state = fields.Selection([('draft', 'Draft'), ('approved', 'Approved'), ('billed', 'Billed')],
                             default='draft', required=True, index=True)
    bill_id = fields.Many2one('account.move', string='Bill', readonly=True)

    @api.model
    def _collected_by_line(self, company, date_from, date_to):
        """{invoice line: (collected amount, date)} for medical invoice lines paid in the period.

        Only reconciliations with a payment or a bank statement line count as collection
        (credit notes reduce the invoice but are not cash in).
        """
        partials = self.env['account.partial.reconcile'].search([
            ('max_date', '>=', date_from), ('max_date', '<=', date_to),
            ('company_id', '=', company.id),
            ('debit_move_id.move_id.medical_move_kind', 'in', ('patient', 'insurer', 'rebill', 'program_contract')),
        ])
        result = defaultdict(lambda: [0.0, False])
        for partial in partials:
            payment_move = partial.credit_move_id.move_id
            if not (payment_move.origin_payment_id or payment_move.statement_line_id):
                continue
            invoice = partial.debit_move_id.move_id
            if invoice.currency_id.is_zero(invoice.amount_total):
                continue
            ratio = partial.amount / abs(invoice.amount_total_signed)
            for line in invoice.invoice_line_ids.filtered('medical_doctor_id'):
                result[line][0] += line.price_total * ratio
                result[line][1] = max(result[line][1] or partial.max_date, partial.max_date)
        return result

    @api.model
    def _invoiced_by_line(self, company, date_from, date_to):
        lines = self.env['account.move.line'].search([
            ('company_id', '=', company.id),
            ('parent_state', '=', 'posted'),
            ('medical_doctor_id', '!=', False),
            ('display_type', '=', 'product'),
            ('move_id.medical_move_kind', 'in', ('patient', 'insurer', 'rebill', 'insurer_refund')),
            ('date', '>=', date_from), ('date', '<=', date_to),
        ])
        return {l: [-l.balance, l.date] for l in lines}

    @api.model
    def _compute_commissions(self, date_from, date_to, doctors=None, company=None):
        company = company or self.env.company
        domain = [('state', '=', 'draft'), ('company_id', '=', company.id),
                  ('date_from', '=', date_from), ('date_to', '=', date_to)]
        if doctors:
            domain.append(('doctor_id', 'in', doctors.ids))
        self.search(domain).unlink()
        source = self._collected_by_line if company.medical_commission_basis == 'collected' else self._invoiced_by_line
        Rule = self.env['medical.commission.rule']
        vals_list = []
        for move_line, (base, date) in source(company, date_from, date_to).items():
            doctor = move_line.medical_doctor_id
            if doctors and doctor not in doctors:
                continue
            rule = Rule._match(doctor, move_line.product_id, company)
            if not rule or company.currency_id.is_zero(base):
                continue
            lab = 0.0
            treatment = move_line.medical_line_id
            if rule.deduct_lab and treatment and 'lab_cost' in treatment._fields and treatment.lab_cost:
                line_total = treatment.price_subtotal or 1.0
                lab = treatment.lab_cost * base / line_total
            amount = company.currency_id.round(max(base - lab, 0.0) * rule.rate / 100.0)
            vals_list.append({
                'doctor_id': doctor.id,
                'date': date,
                'date_from': date_from,
                'date_to': date_to,
                'move_line_id': move_line.id,
                'product_id': move_line.product_id.id,
                'rule_id': rule.id,
                'company_id': company.id,
                'base_amount': company.currency_id.round(base),
                'lab_deduction': company.currency_id.round(lab),
                'rate': rule.rate,
                'amount': amount,
            })
        return self.create(vals_list)

    def action_approve(self):
        self.filtered(lambda l: l.state == 'draft').write({'state': 'approved'})

    def action_create_bills(self):
        lines = self.filtered(lambda l: l.state == 'approved')
        if not lines:
            raise UserError(_("Approve the commission lines first."))
        bills = self.env['account.move']
        for (company, doctor), group in lines.grouped(lambda l: (l.company_id, l.doctor_id)).items():
            company._medical_check_config('medical_commission_expense_account_id', 'medical_commission_journal_id')
            partner = doctor.sudo().work_contact_id
            if not partner:
                raise UserError(_("Doctor %s has no work contact to bill.", doctor.name))
            analytic = doctor._medical_analytic_account()
            date_to = max(group.mapped('date_to') or group.mapped('date'))
            bill = self.env['account.move'].create({
                'move_type': 'in_invoice',
                'journal_id': company.medical_commission_journal_id.id,
                'partner_id': partner.id,
                'invoice_date': date_to,
                'ref': _("Commissions %(doctor)s %(period)s", doctor=doctor.name, period=date_to.strftime('%m/%Y')),
                'medical_move_kind': 'commission',
                'company_id': company.id,
                'invoice_line_ids': [Command.create({
                    'name': _("Commission %(specialty)s", specialty=specialty or _("general")),
                    'quantity': 1,
                    'price_unit': sum(sub.mapped('amount')),
                    'account_id': company.medical_commission_expense_account_id.id,
                    'tax_ids': [Command.clear()],
                    'analytic_distribution': {str(analytic.id): 100.0} if analytic else False,
                    'medical_doctor_id': doctor.id,
                }) for specialty, sub in group.grouped(lambda l: l.product_id.specialty_id.name or '').items()],
            })
            group.write({'bill_id': bill.id, 'state': 'billed'})
            bills |= bill
        return bills
