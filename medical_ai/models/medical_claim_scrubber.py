from collections import defaultdict

from dateutil.relativedelta import relativedelta
from odoo import _, api, fields, models
from odoo.tools import html2plaintext

SETTLED = ('approved', 'partially_approved', 'rejected', 'rebilled', 'written_off', 'resubmitted')
PRIOR_WEIGHT = 5.0  # pseudo-claims pulling a procedure's history toward the insurer's overall rate


class MedicalClaimLine(models.Model):
    _inherit = 'medical.claim.line'

    risk_score = fields.Integer(string='Rejection Risk (%)', readonly=True, copy=False,
                                help='Estimated chance that the insurer rejects this line, from rule checks and '
                                     'this clinic\'s own rejection history.')
    risk_level = fields.Selection([('low', 'Low'), ('medium', 'Medium'), ('high', 'High')],
                                  readonly=True, copy=False, index=True)
    risk_reasons = fields.Text(readonly=True, copy=False)
    risk_checked_at = fields.Datetime(readonly=True, copy=False)
    appeal_text = fields.Text(string='Appeal Letter', copy=False)

    # ------------------------------------------------------------- history
    @api.model
    def _rejection_history(self, lines):
        """Smoothed rejection stats per (insurer, procedure) over the last 12 months of settled claims."""
        since = fields.Date.context_today(self) - relativedelta(months=12)
        domain = [('state', 'in', SETTLED), ('service_date', '>=', since),
                  ('insurer_id', 'in', lines.insurer_id.ids), ('id', 'not in', lines.ids)]
        rejected_domain = domain + [('rejected_amount', '>', 0)]
        totals = dict(((ins.id, prod.id), count) for ins, prod, count in
                      self._read_group(domain, ['insurer_id', 'product_id'], ['__count']))
        rejected = dict(((ins.id, prod.id), count) for ins, prod, count in
                        self._read_group(rejected_domain, ['insurer_id', 'product_id'], ['__count']))
        ins_total = defaultdict(int)
        ins_rejected = defaultdict(int)
        for (ins, _prod), count in totals.items():
            ins_total[ins] += count
        for (ins, _prod), count in rejected.items():
            ins_rejected[ins] += count
        reasons = {}
        for ins, prod, reason, _count in self._read_group(
                rejected_domain + [('reject_reason', '!=', False)], ['insurer_id', 'product_id', 'reject_reason'],
                ['__count'], order='__count desc'):
            reasons.setdefault((ins.id, prod.id), reason)
        return totals, rejected, ins_total, ins_rejected, reasons

    # --------------------------------------------------------------- checks
    def _risk_checks(self, history):
        """List of (probability 0..1, message) for one line."""
        self.ensure_one()
        totals, rejected, ins_total, ins_rejected, reasons = history
        tl = self.treatment_line_id
        policy = self.policy_id
        checks = []
        if not policy.is_valid_on(self.service_date):
            checks.append((0.95, _("The policy was not valid on the service date.")))
        if tl.requires_preauth and not self.approval_no:
            checks.append((0.9, _("This procedure needs pre-authorization and no approval number is attached.")))
        valid_preauth = tl.preauth_line_ids.filtered(lambda p: p.preauth_id.state in ('approved', 'partial'))[-1:]
        expiry = valid_preauth.preauth_id.expiry_date
        if valid_preauth and expiry and expiry < self.service_date:
            checks.append((0.8, _("The approval expired on %s, before the service date.", expiry)))
        if not tl.product_id.insurance_code:
            checks.append((0.35, _("The procedure has no insurance code.")))
        site_missing = tl._site_missing()
        if site_missing:
            checks.append((0.6, _("Site missing: %s", site_missing)))
        rule = policy.plan_id._match_rule(tl.product_id) if policy else False
        if rule and not rule.excluded and tl.price_subtotal:
            expected = tl.price_subtotal * rule.coverage_pct / 100.0
            if rule.max_per_item:
                expected = min(expected, rule.max_per_item)
            if self.currency_id.compare_amounts(self.claimed, expected + 0.01) > 0:
                checks.append((0.5, _("Claimed %(claimed)s is above the plan coverage (%(expected)s).",
                                      claimed=self.currency_id.format(self.claimed),
                                      expected=self.currency_id.format(expected))))
        if rule and rule.excluded:
            checks.append((0.9, _("The plan excludes this procedure.")))
        duplicate_domain = [('id', '!=', self.id), ('patient_id', '=', self.patient_id.id),
                            ('product_id', '=', self.product_id.id), ('service_date', '=', self.service_date),
                            ('state', 'not in', ('resubmitted',))]
        if tl.site_key:
            duplicate_domain.append(('treatment_line_id.site_key', '=', tl.site_key))
        if not self.resubmission_of_id and self.search_count(duplicate_domain, limit=1):
            checks.append((0.7, _("Same procedure already claimed for this patient on the same day.")))
        if rule and rule.frequency_months:
            since = self.service_date - relativedelta(months=rule.frequency_months)
            if self.search_count([('id', '!=', self.id), ('patient_id', '=', self.patient_id.id),
                                  ('product_id', '=', self.product_id.id), ('service_date', '>', since),
                                  ('service_date', '<=', self.service_date),
                                  ('state', 'not in', ('rejected', 'resubmitted'))], limit=1):
                checks.append((0.7, _("Already claimed within the plan's %s-month frequency limit.",
                                      rule.frequency_months)))
        # learned part: this insurer's past decisions on this procedure
        key = (self.insurer_id.id, self.product_id.id)
        base_total = ins_total.get(self.insurer_id.id, 0)
        if base_total:
            base = ins_rejected.get(self.insurer_id.id, 0) / base_total
            n, k = totals.get(key, 0), rejected.get(key, 0)
            rate = (k + PRIOR_WEIGHT * base) / (n + PRIOR_WEIGHT)
            if rate >= 0.05:
                if n >= 3 and k:
                    message = _("%(insurer)s rejected %(k)s of %(n)s similar claims in the last 12 months.",
                                insurer=self.insurer_id.name, k=k, n=n)
                    if reasons.get(key):
                        message += ' ' + _("Most common reason: %s.", reasons[key])
                else:
                    message = _("%(insurer)s rejects about %(rate)s%% of claims overall.",
                                insurer=self.insurer_id.name, rate=round(base * 100))
                checks.append((min(rate, 0.9), message))
        return checks

    def action_scrub(self):
        lines = self.filtered(lambda l: l.state in ('to_claim', 'claimed'))
        if not lines:
            return True
        history = self._rejection_history(lines)
        now = fields.Datetime.now()
        for line in lines:
            checks = line._risk_checks(history)
            keep = 1.0
            for probability, _message in checks:
                keep *= (1.0 - probability)
            score = round((1.0 - keep) * 100)
            level = 'high' if score >= 50 else 'medium' if score >= 20 else 'low'
            ordered = sorted(checks, key=lambda c: -c[0])
            line.write({
                'risk_score': score,
                'risk_level': level,
                'risk_reasons': '\n'.join(f"• {message}" for _p, message in ordered) or False,
                'risk_checked_at': now,
            })
        return True

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.action_scrub()
        return lines

    @api.model
    def _cron_scrub_open_lines(self):
        # stored reasons are written in the clinic's language, not the cron user's
        lang = self.env['medical.ai.service']._clinic_lang()
        self.with_context(lang=lang).search([('state', '=', 'to_claim')]).action_scrub()

    # -------------------------------------------------------------- appeal
    def _appeal_facts(self):
        self.ensure_one()
        tl = self.treatment_line_id
        visit = tl.visit_id
        notes = html2plaintext(visit.sudo().note or '')[:800] if visit else ''
        same_visit = visit.line_ids.filtered(lambda l: l.state == 'executed' and l != tl) if visit else []
        facts = [
            f"Insurer: {self.insurer_id.name}",
            "Member number: {member_no}",
            "Patient: {patient}",
            f"Service date: {self.service_date}",
            f"Procedure: {tl.product_id.name} (code {tl.product_id.insurance_code or 'n/a'})",
            f"Site: {tl.site_label or 'n/a'}",
            "Diagnosis: " + (', '.join(visit.sudo().diagnosis_ids.diagnosis_id.mapped('display_name')) or 'not recorded')
            if visit else "Diagnosis: not recorded",
            f"Pre-authorization number: {self.approval_no or 'none'}",
            f"Claimed: {self.claimed:.2f} {self.currency_id.name}; approved: {self.approved:.2f}",
            f"Insurer's rejection reason: {self.reject_reason or 'not given'}",
        ]
        if same_visit:
            facts.append("Other procedures done the same visit: " + ", ".join(same_visit.mapped('product_id.name')))
        if notes:
            facts.append(f"Doctor's clinical notes: {notes}")
        return facts

    def action_draft_appeal(self):
        service = self.env['medical.ai.service']
        for line in self:
            replacements = service._patient_replacements(line.patient_id)
            values = {
                'patient': line.patient_id.name,
                'member_no': line.member_no or '',
                'doctor': line.doctor_id.sudo().name or '',
                'clinic': self.env.company.name,
            }
            if service._is_configured():
                facts = '\n'.join(service._scrub_text(fact, replacements) for fact in line._appeal_facts())
                system = (
                    "You draft appeal letters from a medical center to a medical insurer about a rejected claim. "
                    "Use only the facts provided; never invent findings, test results or dates. If the facts do not "
                    "justify the procedure, say that supporting documents (reports, clinical notes) are attached for review. "
                    "Formal, polite, under 180 words. Keep these placeholders exactly as written where they belong: "
                    "{patient}, {member_no}, {doctor}, {clinic}. "
                    f"Write in {service._output_language()}. Output only the letter."
                )
                text = service._complete('appeal', system, facts, record=line, max_tokens=600)
            else:
                text = line._appeal_template()
            line.appeal_text = service._restore_placeholders(text, values)
        return True

    def _appeal_template(self):
        self.ensure_one()
        tl = self.treatment_line_id
        return _(
            "To the claims department of %(insurer)s,\n\n"
            "We kindly ask you to review the rejection of the claim for {patient} (member no. {member_no}) "
            "for %(procedure)s%(site)s performed on %(date)s.\n"
            "Reason given: %(reason)s.\n"
            "The treatment was clinically necessary. The supporting reports and clinical notes are attached for your review.\n\n"
            "Regards,\n{doctor}\n{clinic}",
            insurer=self.insurer_id.name,
            procedure=tl.product_id.name,
            site=f" ({tl.site_label})" if tl.site_label else '',
            date=self.service_date,
            reason=self.reject_reason or _("not specified"),
        )


class MedicalClaim(models.Model):
    _inherit = 'medical.claim'

    high_risk_count = fields.Integer(compute='_compute_risk_summary')
    high_risk_amount = fields.Monetary(compute='_compute_risk_summary')

    @api.depends('line_ids.risk_level', 'line_ids.claimed')
    def _compute_risk_summary(self):
        for claim in self:
            risky = claim.line_ids.filtered(lambda l: l.risk_level == 'high')
            claim.high_risk_count = len(risky)
            claim.high_risk_amount = sum(risky.mapped('claimed'))

    def action_scrub(self):
        self.line_ids.action_scrub()
        return True

    def action_submit(self):
        self.line_ids.action_scrub()
        return super().action_submit()

    def action_view_high_risk(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('medical_insurance.action_medical_claim_line')
        action['domain'] = [('claim_id', '=', self.id), ('risk_level', '=', 'high')]
        action['context'] = {}
        return action
