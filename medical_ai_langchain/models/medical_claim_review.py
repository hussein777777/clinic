import json
from typing import Literal

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from .lc_engine import message_text

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.output_parsers import PydanticOutputParser
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - declared in external_dependencies
    BaseModel = object

    def Field(*args, **kwargs):  # noqa: N802
        return None

OWNERS = ('reception', 'doctor', 'insurance_officer', 'billing')


class ClaimFix(BaseModel):
    """One concrete correction to make before sending (or resubmitting) the claim line."""
    action: str = Field(description='What to do, in one short imperative sentence.')
    owner: Literal['reception', 'doctor', 'insurance_officer', 'billing'] = Field(
        description='Who should do it.')


class ClaimReview(BaseModel):
    """Structured review of one insurance claim line."""
    risk_level: Literal['low', 'medium', 'high'] = Field(description='Chance that the insurer rejects the line.')
    summary: str = Field(description='One sentence: the main issue, or that the line looks ready.')
    likely_rejection_reasons: list[str] = Field(default_factory=list, max_length=5,
                                                description='Reasons the insurer is likely to give, most likely first.')
    fixes: list[ClaimFix] = Field(default_factory=list, max_length=5)
    missing_documents: list[str] = Field(default_factory=list, max_length=5,
                                         description='Documents to attach, e.g. X-ray report, clinical notes.')
    appeal_worthy: bool = Field(default=False, description='For a rejected line: is an appeal likely to succeed?')


class MedicalClaimLine(models.Model):
    _inherit = 'medical.claim.line'

    ai_review = fields.Json(string='AI Review Result', readonly=True, copy=False)
    ai_review_level = fields.Selection([('low', 'Low'), ('medium', 'Medium'), ('high', 'High')],
                                       string='AI Review', readonly=True, copy=False)
    ai_review_date = fields.Datetime(string='AI Review Date', readonly=True, copy=False)
    ai_review_html = fields.Html(compute='_compute_ai_review_html', string='AI Review Details')

    @api.depends('ai_review')
    def _compute_ai_review_html(self):
        owners = {
            'reception': _("Reception"), 'doctor': _("Doctor"),
            'insurance_officer': _("Insurance officer"), 'billing': _("Billing"),
        }
        for line in self:
            review = line.ai_review or {}
            if not review:
                line.ai_review_html = False
                continue
            parts = [Markup('<p><strong>%s</strong></p>') % review.get('summary', '')]

            def section(title, items):
                if items:
                    parts.append(Markup('<p class="mb-1"><strong>%s</strong></p><ul>') % title)
                    parts.extend(Markup('<li>%s</li>') % item for item in items)
                    parts.append(Markup('</ul>'))

            section(_("Likely rejection reasons"), review.get('likely_rejection_reasons'))
            section(_("Fix before sending"), [
                Markup('%s <span class="badge text-bg-light">%s</span>') % (
                    fix.get('action', ''), owners.get(fix.get('owner'), fix.get('owner', '')))
                for fix in review.get('fixes') or []])
            section(_("Documents to attach"), review.get('missing_documents'))
            if line.state in ('rejected', 'partially_approved'):
                parts.append(Markup('<p>%s</p>') % (
                    _("An appeal looks worthwhile.") if review.get('appeal_worthy') else _("An appeal is unlikely to succeed.")))
            line.ai_review_html = Markup('').join(parts)

    def _review_facts(self):
        """Anonymized facts about the line for the model: what the rules found and what the history says."""
        self.ensure_one()
        tl = self.treatment_line_id
        visit = tl.visit_id.sudo()
        history = self._rejection_history(self)
        totals, rejected, _ins_total, _ins_rejected, reasons = history
        key = (self.insurer_id.id, self.product_id.id)
        facts = {
            'insurer': self.insurer_id.name,
            'procedure': tl.product_id.name,
            'procedure_code': tl.product_id.insurance_code or None,
            'site': tl.site_label or None,
            'service_date': str(self.service_date),
            'policy_valid_on_service_date': self.policy_id.is_valid_on(self.service_date),
            'needs_preauthorization': bool(tl.requires_preauth),
            'approval_number_attached': bool(self.approval_no),
            'claimed': self.claimed,
            'status': self.state,
            'insurer_rejection_reason': self.reject_reason or None,
            'diagnoses': visit.diagnosis_ids.diagnosis_id.mapped('display_name') if visit else [],
            'clinical_note_present': bool(visit and html2plaintext(visit.note or '').strip()),
            'rule_checks': [r for r in (self.risk_reasons or '').splitlines() if r.strip()],
            'rule_risk_percent': self.risk_score,
            'history_12_months': {
                'same_procedure_claims': totals.get(key, 0),
                'same_procedure_rejections': rejected.get(key, 0),
                'most_common_reason': reasons.get(key),
            },
        }
        return facts

    def action_ai_review(self):
        service = self.env['medical.ai.service']
        service._require_provider()
        system = (
            "You review medical insurance claim lines before they are sent to the insurer (or after a rejection). "
            "Use only the facts given: the rule checks, the clinic's own rejection history and the claim data. "
            "Do not invent clinical findings. Prefer concrete, checkable fixes. "
            f"Write the texts in {service._output_language()}; keep the field names and enum values in English."
        )
        for line in self:
            if not line.risk_checked_at:
                line.action_scrub()
            facts = json.dumps(line._review_facts(), ensure_ascii=False, default=str)
            review = service._lc_structured('claim_review', system, facts, ClaimReview, record=line)
            line.write({
                'ai_review': review.model_dump(),
                'ai_review_level': review.risk_level,
                'ai_review_date': fields.Datetime.now(),
            })
        return True


class MedicalClaim(models.Model):
    _inherit = 'medical.claim'

    def action_ai_review_risky(self):
        lines = self.line_ids.filtered(lambda l: l.risk_level in ('medium', 'high') or l.state == 'rejected')
        if not lines:
            raise UserError(_("No medium- or high-risk line to review in this batch."))
        lines.action_ai_review()
        return True


class MedicalAiService(models.AbstractModel):
    _inherit = 'medical.ai.service'

    @api.model
    def _lc_structured(self, feature, system, prompt, schema, record=None, max_tokens=900):
        """Ask for an answer that validates against the pydantic `schema`.

        Native structured output (tool calling / JSON schema) first; servers without it get format
        instructions and a parser. Either way the result is a validated `schema` instance.
        """
        def runner(svc, usage):
            cfg = svc._get_config()
            llm = svc._lc_chat_model(cfg, max_tokens=max_tokens)
            messages = [SystemMessage(content=system), HumanMessage(content=prompt)]
            if cfg['provider'] != 'openai_compatible':
                try:
                    result = llm.with_structured_output(schema, include_raw=True).invoke(messages)
                except NotImplementedError:
                    result = None
                if result is not None:
                    svc._lc_record_usage(result.get('raw'))
                    if result.get('parsing_error') is not None:
                        raise result['parsing_error']
                    if result.get('parsed') is not None:
                        parsed = result['parsed']
                        return parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
                    if result.get('raw') is not None and message_text(result['raw']).strip():
                        return PydanticOutputParser(pydantic_object=schema).parse(message_text(result['raw']))
            parser = PydanticOutputParser(pydantic_object=schema)
            reply = llm.invoke([SystemMessage(content=system + "\n\n" + parser.get_format_instructions()),
                                HumanMessage(content=prompt)])
            svc._lc_record_usage(reply)
            return parser.parse(message_text(reply))

        return self._lc_run(feature, runner, record=record, prompt_chars=len(system) + len(prompt), prompt=prompt)
