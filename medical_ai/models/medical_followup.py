import re
from urllib.parse import quote

from odoo import _, api, fields, models


class MedicalTreatmentPlan(models.Model):
    _inherit = 'medical.treatment.plan'

    unscheduled_amount = fields.Monetary(string='Not Booked', compute='_compute_unscheduled', store=True,
                                         help='Accepted treatments that are neither booked nor done.')
    unscheduled_count = fields.Integer(compute='_compute_unscheduled', store=True)
    followup_message = fields.Text(string='Follow-up Message', copy=False)
    followup_date = fields.Datetime(string='Last Follow-up', copy=False, readonly=True)
    patient_phone = fields.Char(related='patient_id.phone')

    @api.depends('state', 'line_ids.state', 'line_ids.visit_id', 'line_ids.price_subtotal')
    def _compute_unscheduled(self):
        for plan in self:
            lines = plan.line_ids.filtered(lambda l: l.state == 'planned' and not l.visit_id) \
                if plan.state in ('accepted', 'in_progress') else plan.line_ids.browse()
            plan.unscheduled_amount = sum(lines.mapped('price_subtotal'))
            plan.unscheduled_count = len(lines)

    def _pending_items(self):
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.state == 'planned' and not l.visit_id)
        return [l.display_name for l in lines]

    def _followup_template(self):
        self.ensure_one()
        return _(
            "Hello {patient}, this is {clinic}. Your treatment plan still includes: %(items)s. "
            "Completing it soon keeps the treatment simpler and avoids further damage. "
            "Reply to this message and we will find a time that suits you.",
            items=', '.join(self._pending_items()),
        )

    def action_draft_followup(self):
        service = self.env['medical.ai.service']
        for plan in self:
            if not plan.unscheduled_count:
                continue
            values = {
                'patient': (plan.patient_id.name or '').split(' ')[0],
                'clinic': self.env.company.name,
            }
            if service._is_configured():
                days = (fields.Date.context_today(self) - plan.date).days if plan.date else 0
                prompt = (
                    f"Remaining treatments: {', '.join(plan._pending_items())}.\n"
                    f"Plan accepted {days} days ago."
                )
                system = (
                    "You write a short WhatsApp message from a medical clinic to a patient who accepted a treatment "
                    "plan but has not booked the remaining treatments. Warm and respectful, at most 60 words. "
                    "Explain in one plain sentence why waiting can make treatment harder; do not frighten. "
                    "Do not mention prices, discounts or dates, and do not invent anything. "
                    "Use exactly the placeholders {patient} and {clinic}. "
                    f"Write in {service._output_language()}. Output only the message."
                )
                text = service._complete('followup', system, prompt, record=plan, max_tokens=300)
            else:
                text = plan._followup_template()
            plan.write({
                'followup_message': service._restore_placeholders(text, values),
                'followup_date': fields.Datetime.now(),
            })
        return True

    def action_open_whatsapp(self):
        self.ensure_one()
        if not self.followup_message:
            self.action_draft_followup()
        phone = re.sub(r'\D', '', self.patient_id.phone or '')
        url = f"https://wa.me/{phone}?text={quote(self.followup_message or '')}"
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}
