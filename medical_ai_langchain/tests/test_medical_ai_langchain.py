from datetime import datetime, timedelta
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.medical_core.tests.common import MedicalVisitHelper, setup_medical_data
from odoo.addons.medical_insurance.tests.common import setup_insurance_data
from odoo.addons.medical_ai_langchain.models.lc_engine import LocalHashEmbeddings

CHAT_MODEL = 'odoo.addons.medical_ai_langchain.models.lc_engine.MedicalAiService._lc_chat_model'
HTTP_REQUEST = 'odoo.addons.medical_ai.models.medical_ai_service.MedicalAiService._request'

MRI_POLICY = """<p>MedNet Test - imaging policy</p>
<p>MRI and CT scans need pre-authorization from the insurer before the appointment. Send the request with
the doctor's clinical notes and the previous X-ray report. Approval takes 2 working days.</p>
<p>Plain X-rays and ultrasound do not need pre-authorization.</p>"""
CANCEL_POLICY = """<p>Appointment policy</p><p>Patients who cancel less than 24 hours before the visit are
charged a cancellation fee of 50. Three missed appointments block online booking for 3 months.</p>"""


class ScriptedChatModel(BaseChatModel):
    """Chat model replaying scripted replies; records what it was sent."""
    replies: list = []
    seen: list = []

    @property
    def _llm_type(self):
        return 'scripted'

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(messages)
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def bind_tools(self, tools, **kwargs):
        return self


def usage(i, o):
    return {'input_tokens': i, 'output_tokens': o, 'total_tokens': i + o}


@tagged('post_install', '-at_install', 'medical')
class TestMedicalAiLangchain(MedicalVisitHelper, AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'group_ids': [(4, cls.env.ref('medical_core.group_medical_manager').id),
                                          (4, cls.env.ref('hr.group_hr_user').id)]})
        cls.env.company._medical_setup_accounting()
        setup_medical_data(cls)
        setup_insurance_data(cls)
        (cls.proc_main | cls.proc_consult | cls.proc_major).taxes_id = False
        cls.patient.write({'name': 'Ahmed Ali', 'phone': '+218 91 234 5678', 'medical_alerts': 'Penicillin allergy'})
        params = cls.env['ir.config_parameter'].sudo()
        params.set_param('medical_ai.engine', 'langchain')
        params.set_param('medical_ai.embedding_provider', 'local')
        params.set_param('medical_ai.model', False)
        Doc = cls.env['medical.ai.kb.document']
        cls.doc_mri = Doc.create({'name': 'MedNet imaging policy', 'category': 'insurer_policy', 'body': MRI_POLICY,
                                  'insurer_id': cls.insurer.id})
        cls.doc_cancel = Doc.create({'name': 'Cancellation policy', 'category': 'admin', 'body': CANCEL_POLICY})

    def _configure_ai(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('medical_ai.provider', 'openai')
        params.set_param('medical_ai.model', 'test-model')
        params.set_param('medical_ai.api_key', 'sk-test')

    def _fake(self, *replies):
        model = ScriptedChatModel(replies=list(replies), seen=[])
        return model, patch(CHAT_MODEL, lambda *a, **k: model)

    # ------------------------------------------------------------- engine
    def test_engine_runs_existing_features_through_langchain(self):
        self._configure_ai()
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('medical_ai.price_input', 2.0)
        params.set_param('medical_ai.price_output', 10.0)
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        model, patcher = self._fake(AIMessage(content=[{'type': 'text', 'text': '- {patient} is due for a check-up'}],
                                              usage_metadata=usage(1000, 200)))
        with patcher:
            visit.action_ai_brief()
        sent = ' '.join(m.content for m in model.seen[0])
        self.assertNotIn('Ahmed', sent)
        self.assertNotIn('Penicillin', sent)
        self.assertIn('Ahmed Ali is due for a check-up', visit.ai_brief)
        log = self.env['medical.ai.log'].search([], limit=1)
        self.assertEqual((log.feature, log.engine, log.input_tokens, log.output_tokens), ('brief', 'langchain', 1000, 200))
        self.assertAlmostEqual(log.cost, (1000 * 2.0 + 200 * 10.0) / 1e6)

    def test_http_engine_still_available(self):
        self._configure_ai()
        self.env['ir.config_parameter'].sudo().set_param('medical_ai.engine', 'http')
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        with patch(HTTP_REQUEST, lambda *a, **k: "- direct answer"), \
                patch(CHAT_MODEL, side_effect=AssertionError("LangChain must not be used")):
            visit.action_ai_brief()
        self.assertIn('direct answer', visit.ai_brief)
        self.assertEqual(self.env['medical.ai.log'].search([], limit=1).engine, 'http')

    def test_provider_error_is_logged(self):
        self._configure_ai()
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])

        def boom(messages):
            raise RuntimeError("rate limited")

        _model, patcher = self._fake(boom)
        with patcher:
            try:
                visit.action_ai_brief()
            except UserError:
                pass
            else:
                self.fail("a provider error must surface as a UserError")
        log = self.env['medical.ai.log'].search([], limit=1)
        self.assertEqual((log.state, log.engine), ('error', 'langchain'))
        self.assertIn('rate limited', log.error)

    # ---------------------------------------------------------- knowledge
    def test_local_embeddings_handle_arabic_forms(self):
        emb = LocalHashEmbeddings()
        a, b, c = emb.embed_documents(['الموافقة المسبقة على الأشعة', 'الموافقه المسبقه علي الاشعة', 'رسوم الإلغاء'])
        dot = lambda x, y: sum(p * q for p, q in zip(x, y))  # noqa: E731
        self.assertGreater(dot(a, b), 0.95, "hamza, taa marbuta and alef maqsura variants match")
        self.assertLess(dot(a, c), 0.3)

    def test_knowledge_base_index_search_and_extractive_answer(self):
        (self.doc_mri | self.doc_cancel).action_index()
        self.assertEqual(self.doc_mri.state, 'indexed')
        self.assertTrue(self.doc_mri.chunk_count)
        self.assertEqual(self.doc_mri.embedding_signature, 'local:hash-512')
        hits = self.env['medical.ai.kb.document']._kb_search('Does an MRI need pre-authorization?')
        self.assertEqual(hits[0][0].document_id, self.doc_mri)
        result = self.env['medical.ai.kb.document']._kb_answer('Does an MRI need pre-authorization?')
        self.assertEqual(result['mode'], 'extractive')
        self.assertEqual(result['sources'][0]['document'], 'MedNet imaging policy')
        # editing the document puts it back in the queue
        self.doc_mri.body = MRI_POLICY + '<p>Updated.</p>'
        self.assertEqual(self.doc_mri.state, 'draft')
        self.env['medical.ai.kb.document']._cron_index_pending()
        self.assertEqual(self.doc_mri.state, 'indexed')

    def test_knowledge_base_vector_store_fallback(self):
        """Both stores rank the same way: force the plain-table path and compare."""
        self.doc_mri.action_index()
        Chunk = self.env['medical.ai.kb.chunk']
        vector = LocalHashEmbeddings().embed_query('MRI pre-authorization')
        native = Chunk._nearest(vector, 'local:hash-512', self.doc_mri.ids, 2)
        with patch.object(type(Chunk), '_use_pgvector', lambda self: False):
            table = Chunk._nearest(vector, 'local:hash-512', self.doc_mri.ids, 2)
        self.assertEqual([c.id for c, _s in native], [c.id for c, _s in table])
        self.assertAlmostEqual(native[0][1], table[0][1], places=4)

    def test_knowledge_answer_with_provider_cites_sources(self):
        (self.doc_mri | self.doc_cancel).action_index()
        self._configure_ai()
        model, patcher = self._fake(AIMessage(content="Yes, MRI needs pre-authorization [1].",
                                              usage_metadata=usage(500, 20)))
        with patcher:
            result = self.env['medical.ai.kb.document']._kb_answer('Does an MRI need pre-authorization?')
        self.assertEqual(result['mode'], 'generative')
        self.assertIn('[1]', result['answer'])
        system = model.seen[0][0].content
        self.assertIn('[1] MedNet imaging policy', system)
        self.assertEqual(self.env['medical.ai.log'].search([], limit=1).feature, 'rag')

    def test_knowledge_respects_company(self):
        other = self.env['res.company'].create({'name': 'Other center'})
        self.doc_cancel.company_id = other
        (self.doc_mri | self.doc_cancel).action_index()
        manager = new_test_user(self.env, 'kb_manager', groups='medical_core.group_medical_manager',
                                company_id=self.env.company.id, company_ids=[(6, 0, self.env.company.ids)])
        hits = self.env['medical.ai.kb.document'].with_user(manager)._kb_search('cancellation fee missed appointments')
        self.assertNotIn(self.doc_cancel, [c.document_id for c, _s in hits])
        self.assertIn(self.doc_mri, [c.document_id for c, _s in hits])

    # -------------------------------------------------------------- agent
    def test_agent_uses_tools_and_hides_patient_names(self):
        self.doc_mri.action_index()
        self._configure_ai()
        code = self.patient.patient_code
        visit = self._create_visit([{'product_id': self.proc_consult.id}],
                                   start=datetime.now() + timedelta(days=2))
        visit.action_confirm()

        def first(messages):
            question = messages[-1]
            assert isinstance(question, HumanMessage)
            assert 'Ahmed' not in question.content and code in question.content, question.content
            return AIMessage(content='', tool_calls=[
                {'name': 'patient_overview', 'args': {'file_no': code}, 'id': 'call_1'},
                {'name': 'search_knowledge', 'args': {'question': 'MRI pre-authorization'}, 'id': 'call_2'},
            ], usage_metadata=usage(300, 30))

        def second(messages):
            tool_outputs = [m for m in messages if isinstance(m, ToolMessage)]
            assert len(tool_outputs) == 2
            assert 'Penicillin' not in tool_outputs[0].content, "alerts stay in Odoo"
            assert 'Ahmed' not in tool_outputs[0].content
            assert '"allergies_recorded": true' in tool_outputs[0].content
            return AIMessage(content=f"{code} has a visit in 2 days. MRI needs pre-authorization [KB 1].",
                             usage_metadata=usage(600, 40))

        _model, patcher = self._fake(first, second)
        session = self.env['medical.ai.chat.session'].create({})
        with patcher:
            message = session.ask("When does Ahmed Ali come back, and does he need approval for an MRI?")
        self.assertIn(f'Ahmed Ali ({code})', message['html'])
        self.assertEqual(message['tools'], ['patient_overview', 'search_knowledge'])
        self.assertEqual(message['sources'][0]['document'], 'MedNet imaging policy')
        log = self.env['medical.ai.log'].search([('feature', '=', 'agent')], limit=1)
        self.assertEqual((log.tool_calls, log.input_tokens, log.output_tokens), (2, 900, 70))
        self.assertEqual(session.name[:9], 'When does')
        self.assertEqual(len(session.message_ids), 2)

    def test_agent_tools_respect_access_rights(self):
        reception = new_test_user(self.env, 'reception_ai', groups='medical_core.group_medical_user')
        agent = self.env['medical.ai.agent'].with_user(reception)
        self.assertIn('Only center managers', agent._tool_clinic_numbers('this_month'))
        overview = agent._tool_patient_overview(self.patient.patient_code)
        self.assertNotIn('problems', overview)
        self.assertNotIn('abnormal_results', overview)
        # conversations are private
        session = self.env['medical.ai.chat.session'].create({})
        with self.assertRaises(AccessError):
            session.with_user(reception).read(['name'])

    def test_agent_tools_return_facts(self):
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        visit.action_done()
        agent = self.env['medical.ai.agent']
        schedule = agent._tool_schedule()
        self.assertIn(self.patient.patient_code, schedule)
        self.assertIn('"count": 1', schedule)
        self.assertIn('MedNet Test', agent._tool_claims_overview())
        self.assertIn('kpis', agent._tool_clinic_numbers('this_month'))
        self.assertIn('overdue_tasks', agent._tool_overdue_work())
        self.assertIn('no patient found', agent._tool_find_patient('nobody at all'))

    def test_agent_without_provider_answers_from_knowledge_base(self):
        self.doc_cancel.action_index()
        message = self.env['medical.ai.chat.session'].ask_from_dashboard('What is the cancellation fee?')['message']
        self.assertEqual(message['mode'], 'extractive')
        self.assertEqual(message['sources'][0]['document'], 'Cancellation policy')

    # -------------------------------------------------------- claim review
    def _claim_line(self):
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'left'}])
        visit.action_done()
        return visit.line_ids.claim_line_ids

    def test_claim_review_structured_output(self):
        line = self._claim_line()
        self._configure_ai()
        review = {
            'risk_level': 'high', 'summary': 'Pre-authorization missing.',
            'likely_rejection_reasons': ['Pre-authorization missing'],
            'fixes': [{'action': 'Attach the approval number', 'owner': 'insurance_officer'}],
            'missing_documents': ['Clinical notes'], 'appeal_worthy': False,
        }
        model, patcher = self._fake(AIMessage(content='', tool_calls=[{'name': 'ClaimReview', 'args': review, 'id': 'c1'}],
                                              usage_metadata=usage(400, 80)))
        with patcher:
            line.action_ai_review()
        self.assertEqual(line.ai_review_level, 'high')
        self.assertEqual(line.ai_review['fixes'][0]['owner'], 'insurance_officer')
        self.assertIn('Attach the approval number', line.ai_review_html)
        facts = model.seen[0][-1].content
        self.assertNotIn('Ahmed', facts)
        self.assertIn('needs_preauthorization', facts)
        self.assertEqual(self.env['medical.ai.log'].search([], limit=1).feature, 'claim_review')

    def test_claim_review_rejects_invalid_output(self):
        line = self._claim_line()
        self._configure_ai()
        bad = {'risk_level': 'catastrophic', 'summary': 'x'}
        _model, patcher = self._fake(AIMessage(content='', tool_calls=[{'name': 'ClaimReview', 'args': bad, 'id': 'c1'}]))
        with patcher:
            try:
                line.action_ai_review()
            except UserError:
                pass
            else:
                self.fail("an answer that does not match the schema must be refused")
        self.assertFalse(line.ai_review_level)
        self.assertEqual(self.env['medical.ai.log'].search([], limit=1).state, 'error')

    def test_claim_review_needs_provider(self):
        line = self._claim_line()
        with self.assertRaises(UserError):
            line.action_ai_review()

    # ---------------------------------------------------- smart dashboard
    def test_insights_rule_based_and_ai(self):
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}],
                                   start=datetime.now().replace(hour=8, minute=0, second=0, microsecond=0))
        visit.action_done()
        Dash = self.env['medical.ai.smart.dashboard']
        rules = Dash.generate_insights('this_month')
        self.assertEqual(rules['mode'], 'rules')
        self.assertTrue(any(self.department.name in item['title'] for item in rules['items']))
        self.assertTrue(all(item['severity'] in ('info', 'warning', 'critical') for item in rules['items']))
        self._configure_ai()
        items = {'insights': [{'title': 'Collections lag production', 'detail': '0 of 1,000 collected.',
                               'severity': 'warning', 'action': 'Chase the balance.'}]}
        model, patcher = self._fake(AIMessage(content='', tool_calls=[{'name': 'InsightList', 'args': items, 'id': 'i1'}]))
        with patcher:
            ai = Dash.generate_insights('this_month')
        self.assertEqual(ai['mode'], 'ai')
        self.assertEqual(ai['items'][0]['title'], 'Collections lag production')
        self.assertIn('rule_based_findings', model.seen[0][-1].content)

    def test_dashboard_data(self):
        self.doc_mri.action_index()
        self._configure_ai()
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        _model, patcher = self._fake(AIMessage(content='- ok', usage_metadata=usage(10, 5)))
        with patcher:
            visit.action_ai_brief()
        data = self.env['medical.ai.smart.dashboard'].get_data(7)
        self.assertEqual(data['days'], 7)
        self.assertEqual(len(data['daily']['labels']), 7)
        self.assertEqual(data['usage']['requests'], 1)
        self.assertEqual(data['usage']['input_tokens'], 10)
        self.assertEqual(data['kb']['indexed'], 1)
        self.assertEqual(data['kb']['pending'], 1)
        self.assertIn(data['engine']['vector_store'], ('pgvector', 'table'))
        self.assertTrue(data['engine']['configured'])


@tagged('post_install', '-at_install', 'medical')
class TestSmartDashboardUI(HttpCase):

    def test_smart_dashboard_tour(self):
        self.env['ir.config_parameter'].sudo().set_param('medical_ai.model', False)
        doc = self.env['medical.ai.kb.document'].create({'name': 'Imaging policy', 'category': 'insurer_policy',
                                                         'body': MRI_POLICY})
        doc.action_index()
        admin = self.env.ref('base.user_admin')
        admin.write({'group_ids': [(4, self.env.ref('medical_core.group_medical_manager').id)]})
        action = self.env.ref('medical_ai_langchain.action_medical_ai_smart_dashboard')
        self.start_tour(f"/odoo/action-{action.id}", 'medical_ai_smart_dashboard_tour', login='admin')
