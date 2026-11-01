import base64
import io

import xlsxwriter

from odoo import api, fields, models, _
from odoo.exceptions import UserError

CLAIM_LINE_STATES = [
    ('to_claim', 'To Claim'),
    ('claimed', 'Claimed'),
    ('approved', 'Approved'),
    ('partially_approved', 'Partially Approved'),
    ('rejected', 'Rejected'),
    ('rebilled', 'Re-billed to Patient'),
    ('written_off', 'Written Off'),
    ('resubmitted', 'Resubmitted'),
]


class MedicalClaimLine(models.Model):
    _name = 'medical.claim.line'
    _description = 'Insurance Claim Line'
    _order = 'service_date desc, id desc'

    claim_id = fields.Many2one('medical.claim', string='Claim Batch', index='btree_not_null', ondelete='set null', copy=False)
    treatment_line_id = fields.Many2one('medical.treatment.line', required=True, index=True, ondelete='restrict')
    visit_id = fields.Many2one(related='treatment_line_id.visit_id', store=True)
    patient_id = fields.Many2one('res.partner', required=True, index=True)
    policy_id = fields.Many2one('medical.patient.policy', required=True, index=True)
    insurer_id = fields.Many2one('res.partner', required=True, index=True)
    member_no = fields.Char(related='policy_id.member_no', store=True)
    product_id = fields.Many2one(related='treatment_line_id.product_id', store=True)
    insurance_code = fields.Char(related='treatment_line_id.product_id.insurance_code')
    site_label = fields.Char(related='treatment_line_id.site_label')
    department_id = fields.Many2one(related='treatment_line_id.department_id', store=True)
    doctor_id = fields.Many2one(related='treatment_line_id.doctor_id', store=True)
    approval_no = fields.Char(string='Approval No.')
    service_date = fields.Date(required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    claimed = fields.Monetary(required=True)
    approved = fields.Monetary(help='Amount accepted by the insurer. Defaults to the claimed amount.')
    rejected_amount = fields.Monetary(compute='_compute_rejected_amount', store=True)
    reject_reason = fields.Char()
    state = fields.Selection(CLAIM_LINE_STATES, default='to_claim', required=True, index=True, copy=False)
    resubmission_of_id = fields.Many2one('medical.claim.line', string='Resubmission of', copy=False)

    @api.depends('claimed', 'approved', 'state')
    def _compute_rejected_amount(self):
        for line in self:
            if line.state in ('to_claim', 'claimed'):
                line.rejected_amount = 0.0
            else:
                line.rejected_amount = max(line.claimed - line.approved, 0.0)

    def _compute_display_name(self):
        for line in self:
            line.display_name = f"{line.patient_id.name} - {line.product_id.name} ({line.service_date})"

    def action_resubmit(self):
        new_lines = self.env['medical.claim.line']
        for line in self:
            if line.state not in ('rejected', 'partially_approved'):
                raise UserError(_("Only rejected amounts can be resubmitted."))
            new_lines |= line.copy({
                'claimed': line.rejected_amount,
                'approved': 0.0,
                'reject_reason': False,
                'state': 'to_claim',
                'resubmission_of_id': line.id,
            })
            line.state = 'resubmitted' if line.state == 'rejected' else line.state
        return new_lines


class MedicalClaim(models.Model):
    _name = 'medical.claim'
    _description = 'Insurance Claim Batch'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_to desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    insurer_id = fields.Many2one('res.partner', required=True, index=True, domain=[('is_insurer', '=', True)], tracking=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    line_ids = fields.One2many('medical.claim.line', 'claim_id', string='Claim Lines')
    line_count = fields.Integer(compute='_compute_line_count')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('settled', 'Settled'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    amount_claimed = fields.Monetary(compute='_compute_amounts', store=True)
    amount_approved = fields.Monetary(compute='_compute_amounts', store=True)
    amount_rejected = fields.Monetary(compute='_compute_amounts', store=True)
    settlement_date = fields.Date(copy=False, tracking=True)
    export_file = fields.Binary(attachment=True, copy=False)
    export_filename = fields.Char(copy=False)

    @api.depends('line_ids.claimed', 'line_ids.approved', 'line_ids.rejected_amount', 'line_ids.state')
    def _compute_amounts(self):
        for claim in self:
            claim.amount_claimed = sum(claim.line_ids.mapped('claimed'))
            settled = claim.line_ids.filtered(lambda l: l.state not in ('to_claim', 'claimed'))
            claim.amount_approved = sum(settled.mapped('approved'))
            claim.amount_rejected = sum(settled.mapped('rejected_amount'))

    @api.depends('line_ids')
    def _compute_line_count(self):
        for claim in self:
            claim.line_count = len(claim.line_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.claim') or _('New')
        return super().create(vals_list)

    @api.model
    def _create_batches(self, date_from, date_to, insurers=None, company=None):
        company = company or self.env.company
        domain = [
            ('state', '=', 'to_claim'),
            ('claim_id', '=', False),
            ('service_date', '<=', date_to),
            ('company_id', '=', company.id),
        ]
        if date_from:
            domain.append(('service_date', '>=', date_from))
        if insurers:
            domain.append(('insurer_id', 'in', insurers.ids))
        lines = self.env['medical.claim.line'].search(domain)
        claims = self.browse()
        for insurer in lines.insurer_id:
            insurer_lines = lines.filtered(lambda l: l.insurer_id == insurer)
            claims |= self.create({
                'insurer_id': insurer.id,
                'date_from': date_from or min(insurer_lines.mapped('service_date')),
                'date_to': date_to,
                'company_id': company.id,
                'line_ids': [(6, 0, insurer_lines.ids)],
            })
        return claims

    def action_submit(self):
        for claim in self:
            if claim.state != 'draft':
                raise UserError(_("Only draft claims can be submitted."))
            if not claim.line_ids:
                raise UserError(_("Claim %s has no lines.", claim.name))
        for line in self.line_ids:
            line.write({'state': 'claimed', 'approved': line.claimed})
        self.write({'state': 'submitted'})

    def action_settle(self):
        """Apply the insurer statement: `approved` and `reject_reason` are filled on the lines beforehand."""
        for claim in self:
            if claim.state != 'submitted':
                raise UserError(_("Only submitted claims can be settled."))
            for line in claim.line_ids:
                cmp = line.currency_id.compare_amounts(line.approved, line.claimed)
                if cmp > 0:
                    raise UserError(_("Approved amount cannot exceed the claimed amount (%s).", line.display_name))
                if line.currency_id.is_zero(line.approved):
                    line.state = 'rejected'
                elif cmp < 0:
                    line.state = 'partially_approved'
                else:
                    line.state = 'approved'
            claim.write({'state': 'settled', 'settlement_date': claim.settlement_date or fields.Date.context_today(self)})
        return True

    def action_cancel(self):
        for claim in self:
            if claim.state not in ('draft',):
                raise UserError(_("Only draft claims can be cancelled."))
        self.line_ids.write({'claim_id': False, 'state': 'to_claim'})
        self.write({'state': 'cancelled'})

    # ----------------------------------------------------------------- export
    def _export_columns(self):
        return [
            (_('Member No.'), lambda l: l.member_no or ''),
            (_('Patient'), lambda l: l.patient_id.name or ''),
            (_('Service Date'), lambda l: l.service_date),
            (_('Procedure Code'), lambda l: l.insurance_code or ''),
            (_('Procedure'), lambda l: l.product_id.name or ''),
            (_('Site'), lambda l: l.site_label or ''),
            (_('Approval No.'), lambda l: l.approval_no or ''),
            (_('Doctor'), lambda l: l.doctor_id.name or ''),
            (_('Claimed'), lambda l: l.claimed),
        ]

    def action_export_xlsx(self):
        self.ensure_one()
        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
        sheet = workbook.add_worksheet(self.name[:31].replace('/', '-'))
        bold = workbook.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1})
        date_fmt = workbook.add_format({'num_format': 'yyyy-mm-dd'})
        money = workbook.add_format({'num_format': '#,##0.00'})
        columns = self._export_columns()
        for col, (title, _getter) in enumerate(columns):
            sheet.write(0, col, title, bold)
            sheet.set_column(col, col, 18)
        row = 0
        for row, line in enumerate(self.line_ids.sorted(lambda l: (l.service_date, l.patient_id.name or '')), start=1):
            for col, (_title, getter) in enumerate(columns):
                value = getter(line)
                if isinstance(value, float):
                    sheet.write_number(row, col, value, money)
                elif hasattr(value, 'isoformat'):
                    sheet.write_datetime(row, col, fields.Datetime.to_datetime(value), date_fmt)
                else:
                    sheet.write(row, col, value)
        sheet.write(row + 2, len(columns) - 2, _('Total'), bold)
        sheet.write_number(row + 2, len(columns) - 1, self.amount_claimed, money)
        workbook.close()
        self.write({
            'export_file': base64.b64encode(buffer.getvalue()),
            'export_filename': f"{self.name.replace('/', '-')}.xlsx",
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/medical.claim/{self.id}/export_file/{self.export_filename}?download=true',
            'target': 'self',
        }
