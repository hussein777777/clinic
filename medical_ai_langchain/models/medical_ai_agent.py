import json
import logging
import re
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext

from .lc_engine import message_text
from .medical_ai_kb import excerpt

_logger = logging.getLogger(__name__)

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import StructuredTool
except ImportError:  # pragma: no cover - declared in external_dependencies
    StructuredTool = None

MAX_STEPS = 6
HISTORY_TURNS = 6
CODE_RE = re.compile(r'\bP\d{6}\b')
PERIODS = {'this_month': 'this_month', 'last_month': 'last_month', 'last_90_days': 'last_90', 'this_year': 'this_year'}


def _json(data):
    return json.dumps(data, ensure_ascii=False, default=str)


class MedicalAiChatSession(models.Model):
    _name = 'medical.ai.chat.session'
    _description = 'Clinic Assistant Conversation'
    _order = 'last_message_at desc, id desc'

    name = fields.Char(required=True, default=lambda self: _('New conversation'))
    user_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    department_id = fields.Many2one('medical.department', string='Clinic',
                                    help='Questions in this conversation are about this clinic.')
    message_ids = fields.One2many('medical.ai.chat.message', 'session_id', string='Messages')
    last_message_at = fields.Datetime(readonly=True)
    question = fields.Text(string='Your question', help='Typed in the form, sent with "Ask".')
    transcript = fields.Html(compute='_compute_transcript', sanitize=True)

    @api.depends('message_ids.content', 'message_ids.role')
    def _compute_transcript(self):
        for session in self:
            parts = []
            for message in session.message_ids.sorted('id'):
                who = _("You") if message.role == 'user' else _("Assistant")
                parts.append(f'<div class="o_ai_msg o_ai_msg_{message.role}"><b>{who}</b>'
                             f'{message.content_html or ""}</div>')
            session.transcript = ''.join(parts)

    def action_send(self):
        self.ensure_one()
        if not (self.question or '').strip():
            raise UserError(_("Type a question first."))
        question = self.question
        self.question = False
        self.ask(question)
        return True

    def ask(self, question):
        """Answer one question in this conversation and return the assistant message as a dict."""
        self.ensure_one()
        if self.user_id != self.env.user:
            raise AccessError(_("You can only write in your own conversations."))
        question = (question or '').strip()[:2000]
        if not question:
            raise UserError(_("Type a question first."))
        Message = self.env['medical.ai.chat.message']
        history = self.message_ids.sorted('id')[-HISTORY_TURNS * 2:]
        Message.create({'session_id': self.id, 'role': 'user', 'content': question})
        if self.name == _('New conversation') or not self.message_ids[:-1]:
            self.name = question[:60]
        answer = self.env['medical.ai.agent']._answer(question, history, self.department_id.id)
        reply = Message.create({
            'session_id': self.id, 'role': 'assistant', 'content': answer['text'],
            'sources': answer.get('sources') or [], 'tool_trace': answer.get('trace') or [],
            'mode': answer.get('mode'),
        })
        self.last_message_at = fields.Datetime.now()
        return reply._to_dict()

    @api.model
    def ask_from_dashboard(self, question, session_id=False, department_id=False):
        """Used by the smart dashboard: continue the user's conversation or start a new one."""
        session = self.browse(int(session_id)) if session_id else self.browse()
        if not session.exists() or session.user_id != self.env.user:
            session = self.create({'department_id': int(department_id) if department_id else False})
        message = session.ask(question)
        return {'session_id': session.id, 'message': message}


class MedicalAiChatMessage(models.Model):
    _name = 'medical.ai.chat.message'
    _description = 'Clinic Assistant Message'
    _order = 'id'

    session_id = fields.Many2one('medical.ai.chat.session', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one(related='session_id.user_id', store=True)
    role = fields.Selection([('user', 'User'), ('assistant', 'Assistant')], required=True)
    content = fields.Text()
    content_html = fields.Html(compute='_compute_content_html', sanitize=True)
    sources = fields.Json()
    tool_trace = fields.Json()
    mode = fields.Selection([('agent', 'Assistant with tools'), ('generative', 'Knowledge answer'),
                             ('extractive', 'Passages only'), ('none', 'Nothing found')])

    @api.depends('content', 'role')
    def _compute_content_html(self):
        service = self.env['medical.ai.service']
        for message in self:
            message.content_html = service._text_to_html(message.content or '')

    def _to_dict(self):
        self.ensure_one()
        return {
            'id': self.id,
            'role': self.role,
            'html': str(self.content_html or ''),
            'sources': self.sources or [],
            'tools': [step.get('tool') for step in (self.tool_trace or [])],
            'mode': self.mode,
        }


class MedicalAiAgent(models.AbstractModel):
    _name = 'medical.ai.agent'
    _description = 'Clinic assistant (LangChain tool-calling agent)'

    # ------------------------------------------------------------- privacy
    @api.model
    def _pseudonymize(self, text):
        """Replace patient names typed in a question by their file numbers (phones are masked too)."""
        service = self.env['medical.ai.service']
        if not service._get_config()['anonymize']:
            return text
        words = text.split()[:60]
        Partner = self.env['res.partner']
        done = set()
        for size in (4, 3, 2):
            for start in range(0, max(len(words) - size + 1, 0)):
                phrase = ' '.join(words[start:start + size]).strip('.,؟?!:;()"\'')
                if phrase in done or len(phrase) < 5:
                    continue
                done.add(phrase)
                patient = Partner.search([('is_patient', '=', True), ('name', '=ilike', phrase)], limit=1)
                if patient and patient.patient_code:
                    text = text.replace(phrase, patient.patient_code)
        return service._scrub_text(text)

    @api.model
    def _restore_codes(self, text):
        """Put readable names back next to file numbers, for patients this user can read."""
        codes = set(CODE_RE.findall(text or ''))
        if not codes:
            return text
        patients = self.env['res.partner'].search([('patient_code', 'in', list(codes))])
        names = {p.patient_code: p.name for p in patients}
        return CODE_RE.sub(lambda m: f"{names[m.group(0)]} ({m.group(0)})" if m.group(0) in names else m.group(0),
                           text)

    # --------------------------------------------------------------- helpers
    def _tz(self):
        return pytz.timezone(self.env.user.tz or 'UTC')

    def _day_range(self, day):
        tz = self._tz()
        start = tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        return start, start + timedelta(days=1)

    def _local(self, dt):
        return pytz.utc.localize(dt).astimezone(self._tz()) if dt else None

    def _department(self, name):
        if not name:
            return self.env['medical.department']
        return self.env['medical.department'].search(['|', ('name', 'ilike', name), ('code', '=ilike', name)], limit=1)

    def _patient(self, file_no):
        patient = self.env['res.partner'].search([('patient_code', '=', (file_no or '').strip().upper())], limit=1)
        if not patient:
            raise UserError(_("No patient with file number %s.", file_no))
        return patient

    # ----------------------------------------------------------------- tools
    def _tool_find_patient(self, query: str) -> str:
        """Find patients by file number, name or phone. Returns file numbers, age and visits, never names."""
        domain = [('is_patient', '=', True), '|', '|', ('patient_code', '=ilike', query.strip()),
                  ('name', 'ilike', query.strip()), ('phone', 'ilike', query.strip())]
        patients = self.env['res.partner'].search(domain, limit=8)
        Visit = self.env['medical.visit']
        now = fields.Datetime.now()
        rows = []
        for p in patients:
            upcoming = Visit.search([('patient_id', '=', p.id), ('start', '>=', now),
                                     ('state', 'in', ('draft', 'confirmed'))], order='start', limit=1)
            rows.append({'file_no': p.patient_code, 'age': p.patient_age_label, 'gender': p.gender,
                         'last_visit': str(self._local(p.last_visit_date).date()) if p.last_visit_date else None,
                         'next_visit': str(self._local(upcoming.start)) if upcoming else None})
        return _json(rows or {'result': 'no patient found'})

    def _tool_patient_overview(self, file_no: str) -> str:
        """Clinical overview of one patient (by file number): problems, recent diagnoses, abnormal results,
        regular medicines, care programs and upcoming visits."""
        patient = self._patient(file_no)
        data = {'file_no': patient.patient_code, 'age': patient.patient_age_label, 'gender': patient.gender}
        if self.env.user.has_group('medical_core.group_medical_doctor'):
            data['problems'] = [p.name for p in patient.problem_ids.filtered(lambda p: p.state != 'resolved')]
        data['allergies_recorded'] = bool(patient.sudo().allergen_ids or patient.sudo().medical_alerts)
        can = lambda model: model in self.env and self.env[model].has_access('read')  # noqa: E731
        if can('medical.visit.diagnosis'):
            dx = self.env['medical.visit.diagnosis'].search([('patient_id', '=', patient.id)], order='date desc', limit=5)
            data['recent_diagnoses'] = [f"{d.date.date() if d.date else ''} {d.diagnosis_id.display_name}" for d in dx]
        if self.env.user.has_group('medical_core.group_medical_nurse') and can('medical.observation'):
            obs = self.env['medical.observation'].search([('patient_id', '=', patient.id),
                                                          ('flag', 'not in', ('normal', False))], order='date desc', limit=6)
            data['abnormal_results'] = [f"{o.date.date()} {o.type_id.name} {o.value:g} {o.unit or ''} ({o.flag})"
                                        for o in obs]
        if can('medical.prescription.line'):
            meds = self.env['medical.prescription.line'].search(
                [('prescription_id.patient_id', '=', patient.id), ('prescription_id.state', '=', 'done'),
                 ('is_chronic', '=', True)], limit=8)
            data['regular_medicines'] = list(dict.fromkeys(meds.mapped('name')))
        if can('medical.care.contract'):
            programs = self.env['medical.care.contract'].search([('patient_id', '=', patient.id),
                                                                 ('state', 'in', ('active', 'maintenance'))])
            data['care_programs'] = [f"{c.product_id.name}: {c.sessions_used}/{c.session_count or '-'} sessions"
                                     for c in programs]
        if can('medical.chronic.enrollment'):
            enrollments = self.env['medical.chronic.enrollment'].search([('patient_id', '=', patient.id),
                                                                         ('state', '=', 'active')])
            data['chronic_programs'] = [
                f"{e.program_id.name}: control {e.control_status or 'unknown'}, next review {e.next_review_date}"
                for e in enrollments]
        if can('medical.pregnancy'):
            preg = self.env['medical.pregnancy'].search([('patient_id', '=', patient.id), ('state', '=', 'active')], limit=1)
            if preg:
                data['pregnancy'] = f"{preg.ga_label}, EDD {preg.edd}, risk {preg.risk_level}"
        if can('medical.vaccination'):
            due = self.env['medical.vaccination'].search([
                ('patient_id', '=', patient.id), ('state', '=', 'planned'),
                ('due_date', '<=', fields.Date.context_today(self) + timedelta(days=30))])
            data['vaccines_due'] = [f"{v.display_name} ({v.due_date})" for v in due]
        upcoming = self.env['medical.visit'].search([('patient_id', '=', patient.id), ('start', '>=', fields.Datetime.now()),
                                                     ('state', 'in', ('draft', 'confirmed'))], order='start', limit=3)
        data['upcoming_visits'] = [f"{self._local(v.start):%Y-%m-%d %H:%M} {v.department_id.name}" for v in upcoming]
        return _json(data)

    def _tool_schedule(self, date: str = '', clinic: str = '') -> str:
        """Bookings for one day (YYYY-MM-DD, default today), optionally for one clinic: time, clinic, doctor,
        patient file number and status, with totals."""
        day = fields.Date.to_date(date) if date else fields.Date.context_today(self)
        start, stop = self._day_range(day)
        domain = [('start', '>=', start), ('start', '<', stop), ('state', '!=', 'cancelled')]
        dept = self._department(clinic)
        if dept:
            domain.append(('department_id', '=', dept.id))
        visits = self.env['medical.visit'].search(domain, order='start')
        states = dict(self.env['medical.visit']._fields['state']._description_selection(self.env))
        rows = [{'time': f"{self._local(v.start):%H:%M}", 'clinic': v.department_id.name, 'doctor': v.doctor_id.name,
                 'file_no': v.patient_id.patient_code, 'status': states.get(v.state)} for v in visits[:60]]
        totals = {}
        for v in visits:
            totals[states.get(v.state)] = totals.get(states.get(v.state), 0) + 1
        return _json({'date': str(day), 'clinic': dept.name or 'all', 'count': len(visits), 'by_status': totals,
                      'bookings': rows})

    def _tool_clinic_numbers(self, period: str = 'this_month', clinic: str = '') -> str:
        """Management numbers for a period (this_month, last_month, last_90_days, this_year): production,
        collections, visits, no-show rate, occupancy, insurance rejections, production by clinic and money
        left on the table. Center managers only."""
        if not self.env.user.has_group('medical_core.group_medical_manager'):
            return _json({'error': 'Only center managers can see the center figures.'})
        dept = self._department(clinic)
        data = self.env['medical.dashboard'].get_dashboard_data(PERIODS.get(period, 'this_month'), False, dept.id or False)
        kpis = {k: round(v, 2) if isinstance(v, float) else v for k, v in data['kpis'].items()}
        return _json({
            'period': data['period']['label'], 'compared_with': data['period']['prev_label'],
            'currency': data['currency']['symbol'], 'clinic': dept.name or 'whole center', 'kpis': kpis,
            'production_by_' + data['breakdown']: [{'name': r['name'], 'amount': round(r['amount'], 2)}
                                                   for r in data['by_specialty'][:8]],
            'money_on_the_table': [{'item': r['label'], 'count': r['count'], 'amount': round(r['amount'] or 0, 2)}
                                   for r in data['leakage']],
        })

    def _tool_claims_overview(self, insurer: str = '') -> str:
        """Insurance claim lines: amounts by insurer and status, high-risk lines still to claim and the most
        frequent rejection reasons of the last 6 months."""
        Line = self.env['medical.claim.line']
        domain = [('service_date', '>=', fields.Date.context_today(self) - timedelta(days=183))]
        if insurer:
            domain.append(('insurer_id.name', 'ilike', insurer))
        states = dict(Line._fields['state']._description_selection(self.env))
        by_state = [{'insurer': ins.name, 'status': states.get(state), 'lines': count, 'claimed': round(claimed, 2),
                     'rejected': round(rejected, 2)}
                    for ins, state, count, claimed, rejected in Line._read_group(
                        domain, ['insurer_id', 'state'], ['__count', 'claimed:sum', 'rejected_amount:sum'])]
        reasons = [{'reason': reason, 'lines': count} for reason, count in Line._read_group(
            domain + [('reject_reason', '!=', False)], ['reject_reason'], ['__count'], order='__count desc', limit=5)]
        risky = Line.search_count(domain + [('state', '=', 'to_claim'), ('risk_level', '=', 'high')])
        return _json({'by_insurer_and_status': by_state, 'top_rejection_reasons': reasons,
                      'high_risk_lines_to_claim': risky})

    def _tool_overdue_work(self) -> str:
        """Overdue follow-up tasks (cast removals, vaccinations, chronic reviews, result reviews...), follow-ups
        not booked and lab or imaging results still waiting."""
        today = fields.Date.context_today(self)
        Activity = self.env['mail.activity']
        domain = [('res_model', 'like', 'medical.%'), ('date_deadline', '<', today)]
        overdue = [{'type': summary or 'task', 'count': count} for summary, count in Activity._read_group(
            domain, ['summary'], ['__count'], order='__count desc', limit=10)]
        Visit = self.env['medical.visit']
        follow = Visit.search_count([('follow_up_date', '!=', False), ('follow_up_date', '<=', today + timedelta(days=7)),
                                     ('follow_up_visit_id', '=', False), ('state', 'in', ('done', 'invoiced'))])
        waiting = self.env['medical.order'].search_count([('state', 'in', ('requested', 'in_progress')),
                                                          ('date', '<', fields.Datetime.now() - timedelta(days=2))])
        return _json({'overdue_tasks': overdue, 'follow_ups_not_booked': follow, 'results_waiting_over_2_days': waiting})

    def _tool_search_knowledge(self, question: str) -> str:
        """Search the center's knowledge base (protocols, insurer policies, prices, admin procedures, FAQs).
        Returns numbered passages; cite them as [KB n]."""
        Doc = self.env['medical.ai.kb.document']
        hits = Doc._relevant(Doc._kb_search(question, department_id=self.env.context.get('agent_department_id')))
        sources = self.env.context.get('agent_sources')
        rows = []
        for chunk, score in hits:
            n = len(sources) + 1 if sources is not None else len(rows) + 1
            entry = {'n': n, 'document_id': chunk.document_id.id, 'document': chunk.document_id.name,
                     'page': chunk.page, 'excerpt': excerpt(chunk.content, 400), 'score': round(score, 3)}
            if sources is not None:
                sources.append(entry)
            rows.append({'kb': n, 'document': chunk.document_id.name, 'page': chunk.page, 'text': chunk.content})
        return _json(rows or {'result': 'nothing relevant in the knowledge base'})

    @api.model
    def _tool_specs(self):
        return [
            ('find_patient', self._tool_find_patient),
            ('patient_overview', self._tool_patient_overview),
            ('schedule', self._tool_schedule),
            ('clinic_numbers', self._tool_clinic_numbers),
            ('claims_overview', self._tool_claims_overview),
            ('overdue_work', self._tool_overdue_work),
            ('search_knowledge', self._tool_search_knowledge),
        ]

    @api.model
    def _build_tools(self, trace):
        tools = []
        for name, method in self._tool_specs():
            def run(*args, _method=method, _name=name, **kwargs):
                trace.append({'tool': _name, 'args': kwargs})
                try:
                    with self.env.cr.savepoint():
                        return _method(*args, **kwargs)
                except (UserError, AccessError, ValueError) as error:
                    return _json({'error': str(error)})
            description = ' '.join(method.__doc__.split())
            # the arguments schema comes from the method's signature, the call goes through the wrapper
            schema = StructuredTool.from_function(method, name=name, description=description).args_schema
            tools.append(StructuredTool(name=name, description=description, args_schema=schema, func=run))
        return tools

    # ----------------------------------------------------------------- answer
    @api.model
    def _system_prompt(self, department):
        service = self.env['medical.ai.service']
        now = self._local(fields.Datetime.now())
        return (
            f"You are the assistant of {self.env.company.name}, a medical center. You help staff with the "
            "schedule, patients, results, insurance claims, follow-up work and the center's own documents. "
            "Use the tools to get facts; never invent numbers, names, results or policies. Patients are identified "
            "only by file numbers like P000123: refer to them that way. You do not diagnose or prescribe; for "
            "clinical decisions point to the treating doctor. If a tool returns an error or nothing, say what is "
            "missing. Cite knowledge-base passages as [KB n]. Be short and concrete; use bullets for lists. "
            f"Today is {now:%A %Y-%m-%d}, time {now:%H:%M}. "
            + (f"The user works in the {department.name} clinic. " if department else '')
            + f"Answer in {service._output_language()}."
        )

    @api.model
    def _answer(self, question, history, department_id=False):
        service = self.env['medical.ai.service']
        if not (service._is_configured() and service._lc_enabled()):
            # no provider: the knowledge base still answers with its closest passages
            result = self.env['medical.ai.kb.document']._kb_answer(question, department_id=department_id)
            return {'text': result['answer'], 'sources': result['sources'], 'mode': result['mode'], 'trace': []}
        department = self.env['medical.department'].browse(department_id) if department_id else None
        safe_question = self._pseudonymize(question)
        trace, sources = [], []
        agent = self.with_context(agent_sources=sources, agent_department_id=department_id)
        tools = agent._build_tools(trace)
        by_name = {tool.name: tool for tool in tools}
        messages = [SystemMessage(content=self._system_prompt(department))]
        for message in history:
            text = message.content or ''
            messages.append(HumanMessage(content=self._pseudonymize(text)) if message.role == 'user'
                            else AIMessage(content=text))
        messages.append(HumanMessage(content=safe_question))

        def runner(svc, usage):
            llm = svc._lc_chat_model(svc._get_config(), max_tokens=1000).bind_tools(tools)
            for _step in range(MAX_STEPS):
                reply = llm.invoke(messages)
                svc._lc_record_usage(reply)
                messages.append(reply)
                calls = getattr(reply, 'tool_calls', None) or []
                if not calls:
                    return message_text(reply).strip()
                for call in calls:
                    tool = by_name.get(call['name'])
                    output = tool.invoke(call.get('args') or {}) if tool else _json({'error': 'unknown tool'})
                    usage['tool_calls'] += 1
                    messages.append(ToolMessage(content=str(output), tool_call_id=call.get('id') or call['name']))
            return _("I could not finish within %s steps. Please ask a narrower question.", MAX_STEPS)

        text = service._lc_run('agent', runner, prompt_chars=len(safe_question), prompt=safe_question)
        cited = {int(n) for n in re.findall(r'\[KB\s*(\d+)\]', text)}
        used_sources = [s for s in sources if s['n'] in cited] or sources[:3]
        return {'text': self._restore_codes(text), 'sources': used_sources, 'mode': 'agent', 'trace': trace}
