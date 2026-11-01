from markupsafe import Markup

from odoo import _, fields, models
from odoo.tools import html2plaintext



class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    ai_brief = fields.Html(string='Pre-visit Brief', readonly=True, copy=False, sanitize=True,
                           groups='medical_core.group_medical_doctor')
    ai_brief_date = fields.Datetime(readonly=True, copy=False, groups='medical_core.group_medical_doctor')

    # ------------------------------------------------------------------ facts
    def _brief_facts(self):
        """Structured facts about the patient, split into what is safe to send and what stays local."""
        self.ensure_one()
        patient = self.patient_id
        Line = self.env['medical.treatment.line']
        history = Line.search([('patient_id', '=', patient.id), ('state', '=', 'executed'),
                               ('id', 'not in', self.line_ids.ids)], order='date_executed desc', limit=10)
        pending = Line.search([('patient_id', '=', patient.id), ('state', 'in', ('planned', 'scheduled')),
                               ('plan_id.state', 'in', ('accepted', 'in_progress')),
                               ('id', 'not in', self.line_ids.ids)])
        past_notes = self.search([('patient_id', '=', patient.id), ('id', '!=', self.id),
                                  ('state', 'in', ('done', 'invoiced')), ('note', '!=', False)],
                                 order='start desc', limit=3)
        problems = patient.sudo().problem_ids.filtered(lambda p: p.state != 'resolved')
        abnormal = self.env['medical.observation'].sudo().search(
            [('patient_id', '=', patient.id), ('flag', 'not in', ('normal', False)),
             ('category', '!=', 'vital')], order='date desc', limit=6)
        chronic_meds = self.env['medical.prescription.line'].sudo().search(
            [('prescription_id.patient_id', '=', patient.id), ('prescription_id.state', '=', 'done'),
             ('is_chronic', '=', True)], order='id desc', limit=8)
        last_dx = self.env['medical.visit.diagnosis'].sudo().search(
            [('patient_id', '=', patient.id), ('visit_id', '!=', self.id)], order='date desc', limit=5)
        no_shows = self.search_count([('patient_id', '=', patient.id), ('state', '=', 'no_show')])
        last_visit = self.search([('patient_id', '=', patient.id), ('id', '!=', self.id),
                                  ('state', 'in', ('done', 'invoiced'))], order='start desc', limit=1)
        facts = {
            'age': patient.patient_age_label or 'unknown age',
            'gender': patient.gender,
            'department': self.department_id.name or '',
            'complaint': self.sudo().chief_complaint or '',
            'today': [l.display_name for l in self.line_ids],
            'history': [f"{l.date_executed}: {l.display_name}" for l in history],
            'pending': [l.display_name +
                        (f" [lab: {l.lab_stage}]" if 'lab_stage' in l._fields and l.lab_stage else '')
                        for l in pending],
            'problems': [p.name + (f" ({p.onset_date.year})" if p.onset_date else '') for p in problems],
            'diagnoses': [f"{d.date.date() if d.date else ''}: {d.diagnosis_id.display_name}" for d in last_dx],
            'medicines': list(dict.fromkeys(m.name for m in chronic_meds)),
            'results': [f"{o.date.date()}: {o.type_id.name} {o.value:g} {o.unit or ''} ({o.flag})" for o in abnormal],
            'specialty': self._brief_specialty_facts(),
            'notes': [f"{(n.start.date() if n.start else '')}: {html2plaintext(n.note)[:400]}" for n in past_notes],
            'no_shows': no_shows,
            'last_visit': last_visit.start.date() if last_visit.start else None,
        }
        # local-only facts: never sent to the provider
        local = {
            'alerts': patient.sudo().medical_alerts or '',
            'allergies': ', '.join(patient.sudo().allergen_ids.mapped('name')),
            'overdue': 0.0,
            'policy': self.policy_id,
            'preauth_missing': self.line_ids.filtered(
                lambda l: l.requires_preauth and not l.preauth_line_id and l.insurance_amount),
            'program': self.care_contract_id or self.env['medical.care.contract'].search(
                [('patient_id', '=', patient.id), ('state', 'in', ('active', 'maintenance'))], limit=1),
        }
        overdue_lines = self.env['account.move.line'].sudo().search([
            ('partner_id', '=', patient.id), ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable'), ('reconciled', '=', False),
            ('date_maturity', '<', fields.Date.context_today(self))])
        local['overdue'] = sum(overdue_lines.mapped('amount_residual'))
        return facts, local

    def _brief_header(self, facts, local):
        """Always-on, deterministic part of the brief: things the doctor must not miss."""
        currency = self.company_id.currency_id
        items = []
        if local['allergies']:
            items.append(Markup('<li><strong>%s</strong> %s</li>') % (_("Allergies:"), local['allergies']))
        if local['alerts']:
            items.append(Markup('<li><strong>%s</strong> %s</li>') % (_("Medical alert:"), local['alerts']))
        if local['preauth_missing']:
            items.append(Markup('<li><strong>%s</strong> %s</li>') % (
                _("Pre-authorization missing:"), ', '.join(local['preauth_missing'].mapped('display_name'))))
        if local['overdue'] > 0:
            items.append(Markup('<li><strong>%s</strong> %s</li>') % (_("Overdue balance:"), currency.format(local['overdue'])))
        policy = local['policy']
        if policy:
            remaining = policy._get_remaining_cap()
            text = f"{policy.display_name}"
            if remaining is not None:
                text += ' — ' + _("%s left this year", currency.format(remaining))
            items.append(Markup('<li><strong>%s</strong> %s</li>') % (_("Insurance:"), text))
        if local['program']:
            program = local['program']
            if program.billing_mode == 'sessions':
                text = _("%(name)s, session %(n)s of %(t)s", name=program.name, n=program.sessions_used + 1,
                         t=program.session_count)
            else:
                text = _("%(name)s, month %(m)s of %(t)s", name=program.name, m=program.months_elapsed,
                         t=program.est_months)
            if 'overdue_amount' in program._fields and program.overdue_amount:
                text += ' — ' + _("%s overdue", currency.format(program.overdue_amount))
            items.append(Markup('<li><strong>%s</strong> %s</li>') % (_("Care program:"), text))
        if facts['no_shows']:
            items.append(Markup('<li><strong>%s</strong> %s</li>') % (_("Missed visits:"), facts['no_shows']))
        if not items:
            return Markup('')
        return Markup('<div class="alert alert-warning mb-2"><ul class="mb-0">%s</ul></div>') % Markup('').join(items)

    def _brief_fallback(self, facts):
        sections = []
        blocks = [(_("Complaint"), [facts['complaint']] if facts['complaint'] else []),
                  (_("Today"), facts['today']), (_("Problem list"), facts['problems']),
                  (_("Long-term medicines"), facts['medicines']), (_("Recent abnormal results"), facts['results']),
                  (_("Planned, not done yet"), facts['pending']), (_("Recent treatments"), facts['history'][:6]),
                  (_("Recent diagnoses"), facts['diagnoses'])] + list(facts['specialty'])
        for title, rows in blocks:
            if rows:
                sections.append(Markup('<p><strong>%s</strong></p><ul>%s</ul>') % (
                    title, Markup('').join(Markup('<li>%s</li>') % row for row in rows)))
        return Markup('').join(sections) or Markup('<p>%s</p>') % _("First visit: no history yet.")

    def action_ai_brief(self):
        service = self.env['medical.ai.service']
        for visit in self:
            facts, local = visit._brief_facts()
            header = visit._brief_header(facts, local)
            if service._is_configured():
                replacements = service._patient_replacements(visit.patient_id)
                gender = {'male': 'male', 'female': 'female'}.get(facts['gender'], 'unknown')
                lines = [
                    f"Clinic: {facts['department']}.",
                    f"Patient: {facts['age']}, {gender}.",
                    f"Last visit: {facts['last_visit'] or 'none'}. Missed visits: {facts['no_shows']}.",
                    "Reason for today's visit: " + (facts['complaint'] or 'not recorded'),
                    "Planned for today: " + ('; '.join(facts['today']) or 'nothing recorded'),
                    "Long-term problems: " + ('; '.join(facts['problems']) or 'none recorded'),
                    "Long-term medicines: " + ('; '.join(facts['medicines']) or 'none recorded'),
                    "Recent abnormal results: " + ('; '.join(facts['results']) or 'none'),
                    "Recent diagnoses: " + ('; '.join(facts['diagnoses']) or 'none'),
                    "Accepted but not done: " + ('; '.join(facts['pending']) or 'none'),
                    "Recent treatments: " + ('; '.join(facts['history']) or 'none'),
                ] + [f"{title}: " + '; '.join(rows) for title, rows in facts['specialty'] if rows] + [
                    "Previous clinical notes:\n" + ('\n'.join(facts['notes']) or 'none'),
                ]
                prompt = service._scrub_text('\n'.join(lines), replacements)
                system = (
                    "You prepare a pre-visit brief for a doctor who has 30 seconds before the patient sits down. "
                    "Use only the facts given. Do not diagnose, do not suggest medication or doses, do not invent "
                    "findings. Write 3 to 6 short bullet points: relevant history and long-term problems, what is "
                    "planned today, abnormal results or open treatments worth raising, anything in the notes to "
                    "double-check. "
                    f"Write in {service._output_language()}. Output only the bullets."
                )
                body = service._text_to_html(service._complete('brief', system, prompt, record=visit, max_tokens=500))
                body = Markup(service._restore_placeholders(str(body), {'patient': visit.patient_id.name}))
            else:
                body = visit._brief_fallback(facts)
            visit.sudo().write({'ai_brief': header + body, 'ai_brief_date': fields.Datetime.now()})
        return True
