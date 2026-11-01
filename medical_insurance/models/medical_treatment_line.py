from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MedicalTreatmentLine(models.Model):
    _inherit = 'medical.treatment.line'

    policy_id = fields.Many2one('medical.patient.policy', string='Insurance Policy',
                                compute='_compute_policy_id', store=True, readonly=False, precompute=True,
                                domain="[('patient_id', '=', patient_id)]")
    insurance_excluded = fields.Boolean(string='Cash (not claimed)', copy=False,
                                        help='The patient pays this line in full; it is not sent to the insurer.')
    coverage_pct = fields.Float(string='Coverage (%)', compute='_compute_insurance_split', store=True)
    insurance_amount = fields.Monetary(string='Insurer Share', compute='_compute_insurance_split',
                                       store=True, readonly=False)
    patient_amount = fields.Monetary(string='Patient Share', compute='_compute_patient_amount', store=True)
    requires_preauth = fields.Boolean(compute='_compute_insurance_split', store=True)
    preauth_line_ids = fields.One2many('medical.preauth.line', 'treatment_line_id', string='Pre-authorizations')
    preauth_line_id = fields.Many2one('medical.preauth.line', compute='_compute_preauth_line_id',
                                      string='Valid Pre-authorization')
    approval_no = fields.Char(compute='_compute_preauth_line_id', string='Approval No.')
    preauth_status = fields.Selection([
        ('not_needed', 'Not needed'),
        ('missing', 'Missing'),
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], compute='_compute_preauth_status')
    claim_line_ids = fields.One2many('medical.claim.line', 'treatment_line_id', string='Claim Lines')

    # ------------------------------------------------------------------ policy
    @api.depends('visit_id.policy_id', 'plan_id.policy_id')
    def _compute_policy_id(self):
        for line in self:
            if line.state == 'executed':
                continue
            line.policy_id = line.visit_id.policy_id or line.plan_id.policy_id or line.policy_id

    def _get_service_date(self):
        self.ensure_one()
        if self.date_executed:
            return self.date_executed
        if self.visit_id.start:
            return self.visit_id.start.date()
        return fields.Date.context_today(self)

    def _get_medical_base_price(self):
        price = super()._get_medical_base_price()
        pricelist = self.policy_id.plan_id.pricelist_id
        if pricelist and self.product_id and not self.insurance_excluded:
            price = pricelist._get_product_price(self.product_id, 1.0, date=self._get_service_date())
        return price

    # ------------------------------------------------------------------- split
    def _preauth_needed(self, rule):
        self.ensure_one()
        plan = self.policy_id.plan_id
        threshold = plan.preauth_threshold
        return bool(rule.requires_preauth or
                    (threshold and self.currency_id.compare_amounts(self.price_subtotal, threshold) > 0))

    def _frequency_exceeded(self, rule):
        self.ensure_one()
        if not rule.frequency_months:
            return False
        since = self._get_service_date() - relativedelta(months=rule.frequency_months)
        domain = [
            ('id', '!=', self._origin.id or 0),
            ('patient_id', '=', self.patient_id.id),
            ('product_id', '=', self.product_id.id),
            ('state', '=', 'executed'),
            ('date_executed', '>', since),
            ('insurance_amount', '>', 0),
        ]
        if self.site_key and self.product_id.site_requirement != 'none':
            domain.append(('site_key', '=', self.site_key))
        return bool(self.search_count(domain, limit=1))

    @api.depends('price_subtotal', 'policy_id', 'insurance_excluded', 'product_id', 'site_key',
                 'preauth_line_ids.approved_amount', 'preauth_line_ids.preauth_id.state')
    def _compute_insurance_split(self):
        for line in self:
            if line.state == 'executed' and line.claim_line_ids:
                continue  # frozen once claimed
            coverage, covered, needs_preauth = 0.0, 0.0, False
            policy = line.policy_id
            date = line._get_service_date()
            if policy and not line.insurance_excluded and line.product_id and policy.is_valid_on(date):
                rule = policy.plan_id._match_rule(line.product_id)
                waiting_ok = not rule.waiting_days or date >= policy.date_from + relativedelta(days=rule.waiting_days)
                if rule and not rule.excluded and waiting_ok and not line._frequency_exceeded(rule):
                    coverage = rule.coverage_pct
                    covered = line.price_subtotal * coverage / 100.0
                    if rule.max_per_item:
                        covered = min(covered, rule.max_per_item)
                    cap = policy._get_remaining_cap()
                    if cap is not None:
                        covered = min(covered, cap)
                    needs_preauth = line._preauth_needed(rule)
                    if needs_preauth:
                        valid = line.preauth_line_ids.filtered('is_valid')[-1:]
                        if valid:
                            covered = min(covered, valid.approved_amount)
                        elif line.preauth_line_ids.filtered(lambda p: p.preauth_id.state == 'rejected') \
                                and not line.preauth_line_ids.filtered(lambda p: p.preauth_id.state in ('draft', 'sent')):
                            covered = 0.0
            line.coverage_pct = coverage
            line.insurance_amount = line.currency_id.round(covered) if line.currency_id else covered
            line.requires_preauth = needs_preauth

    @api.depends('price_subtotal', 'insurance_amount')
    def _compute_patient_amount(self):
        for line in self:
            line.patient_amount = line.price_subtotal - line.insurance_amount

    def _recompute_insurance(self):
        lines = self.filtered(lambda l: not l.claim_line_ids)
        lines._compute_insurance_split()

    @api.depends('preauth_line_ids.is_valid', 'preauth_line_ids.preauth_id.approval_no')
    def _compute_preauth_line_id(self):
        for line in self:
            line.preauth_line_id = line.preauth_line_ids.filtered('is_valid')[-1:]
            line.approval_no = line.preauth_line_id.preauth_id.approval_no

    @api.depends('requires_preauth', 'preauth_line_ids.preauth_id.state', 'preauth_line_id')
    def _compute_preauth_status(self):
        for line in self:
            if not line.requires_preauth:
                line.preauth_status = 'not_needed'
            elif line.preauth_line_id:
                line.preauth_status = 'approved'
            elif line.preauth_line_ids.filtered(lambda p: p.preauth_id.state in ('draft', 'sent')):
                line.preauth_status = 'pending'
            elif line.preauth_line_ids.filtered(lambda p: p.preauth_id.state == 'rejected'):
                line.preauth_status = 'rejected'
            else:
                line.preauth_status = 'missing'

    # ---------------------------------------------------------------- actions
    def action_convert_to_cash(self):
        self.filtered(lambda l: l.state != 'executed').write({'insurance_excluded': True})
        self._recompute_prices()

    def _medical_check_before_execute(self):
        super()._medical_check_before_execute()
        blocked = self.filtered(lambda l: l.requires_preauth and l.insurance_amount > 0 and not l.preauth_line_id)
        if blocked:
            raise UserError(_(
                "Pre-authorization is missing for: %s.\n"
                "Record the insurer approval, or switch the line to cash.",
                ", ".join(blocked.mapped('display_name'))))

    def _prepare_claim_line_vals(self):
        self.ensure_one()
        return {
            'treatment_line_id': self.id,
            'patient_id': self.patient_id.id,
            'policy_id': self.policy_id.id,
            'insurer_id': self.policy_id.insurer_id.id,
            'approval_no': self.approval_no,
            'service_date': self.date_executed or self._get_service_date(),
            'claimed': self.insurance_amount,
            'company_id': self.company_id.id,
        }
