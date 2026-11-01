from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.medical_core.tests.common import MedicalVisitHelper, setup_medical_data
from odoo.addons.medical_insurance.tests.common import setup_insurance_data

SERVICE = 'odoo.addons.medical_ai.models.medical_ai_service.MedicalAiService._request'


@tagged('post_install', '-at_install', 'medical')
class TestMedicalAi(MedicalVisitHelper, AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'group_ids': [(4, cls.env.ref('medical_core.group_medical_manager').id),
                                          (4, cls.env.ref('hr.group_hr_user').id)]})
        cls.env.company._medical_setup_accounting()
        setup_medical_data(cls)
        setup_insurance_data(cls)
        (cls.proc_main | cls.proc_consult | cls.proc_major).taxes_id = False
        cls.proc_main.insurance_code = 'D2391'
        cls.proc_major.insurance_code = 'D2740'
        cls.patient.write({'name': 'Ahmed Ali', 'phone': '+218 91 234 5678', 'medical_alerts': 'Penicillin allergy'})

    def _configure_ai(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('medical_ai.provider', 'openai')
        params.set_param('medical_ai.model', 'test-model')
        params.set_param('medical_ai.api_key', 'sk-test')
        # these tests cover the direct HTTP transport; medical_ai_langchain tests its own engine
        params.set_param('medical_ai.engine', 'http')

    # ------------------------------------------------------------ scrubber
    def test_scrubber_flags_missing_preauth_and_history(self):
        visit = self._create_visit([{'product_id': self.proc_major.id, 'laterality': 'right'}], room_id=False)
        self.assertTrue(visit.line_ids.requires_preauth)
        ok_visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        ok_visit.action_done()
        line = ok_visit.line_ids.claim_line_ids
        self.assertEqual(line.risk_level, 'low', line.risk_reasons)
        self.assertFalse(line.risk_reasons)

        # history: the insurer rejected this procedure 4 times out of 5
        for i in range(5):
            v = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'left'}],
                                   start=datetime.now() - timedelta(days=60 + i))
            v.action_done()
        old = self.env['medical.claim.line'].search([('id', '!=', line.id), ('product_id', '=', self.proc_main.id)])
        claim = self.env['medical.claim'].create({'insurer_id': self.insurer.id, 'date_from': fields.Date.today() - timedelta(days=90),
                                                 'date_to': fields.Date.today(), 'line_ids': [(6, 0, old.ids)]})
        claim.action_submit()
        for cl in old[:4]:
            cl.write({'approved': 0.0, 'reject_reason': 'Missing X-ray'})
        claim.action_settle()

        line.action_scrub()
        self.assertGreaterEqual(line.risk_score, 50)
        self.assertEqual(line.risk_level, 'high')
        self.assertIn('Missing X-ray', line.risk_reasons)

        # missing pre-authorization is a hard flag
        crown_line = self.env['medical.claim.line'].create({
            'treatment_line_id': visit.line_ids.id, 'patient_id': self.patient.id, 'policy_id': self.policy.id,
            'insurer_id': self.insurer.id, 'service_date': fields.Date.today(), 'claimed': 2000.0})
        self.assertEqual(crown_line.risk_level, 'high')
        self.assertIn('pre-authorization', crown_line.risk_reasons)

    def test_scrubber_duplicate(self):
        v = self._create_visit([{'product_id': self.proc_consult.id}])
        v.action_done()
        first = v.line_ids.claim_line_ids
        dup = first.copy({'claim_id': False})
        self.assertIn('same day', dup.risk_reasons)

    # ------------------------------------------------------------ no-show
    def test_noshow_risk_follows_patient_history(self):
        reliable = self.env['res.partner'].create({'name': 'Reliable Patient', 'is_patient': True})
        flaky = self.env['res.partner'].create({'name': 'Flaky Patient', 'is_patient': True})
        base_start = datetime.now() - timedelta(days=200)
        for i in range(6):
            for patient, missed in ((reliable, False), (flaky, i > 0)):
                v = self._create_visit([{'product_id': self.proc_consult.id}], patient_id=patient.id,
                                       start=base_start + timedelta(days=i * 10, hours=i if patient == flaky else 0),
                                       room_id=False)
                if missed:
                    v.action_no_show()
                else:
                    v.action_done()
        soon = datetime.now() + timedelta(days=2)
        v_ok = self._create_visit(patient_id=reliable.id, start=soon, room_id=False)
        v_bad = self._create_visit(patient_id=flaky.id, start=soon + timedelta(hours=1), room_id=False)
        self.assertGreater(v_bad.noshow_risk, v_ok.noshow_risk)
        self.assertIn('Missed 5 of 6', v_bad.noshow_factors)
        self.assertEqual(v_bad.noshow_level, 'high')
        before = v_bad.noshow_risk
        v_bad.action_confirm()
        self.assertLess(v_bad.noshow_risk, before, "confirming lowers the risk")
        result = self.env['medical.visit']._noshow_backtest(months=12)
        self.assertEqual(result['visits'], 12)
        self.assertGreaterEqual(result['top20_rate'], result['no_show_rate'])

    # ------------------------------------------------------------ brief
    def test_brief_without_provider(self):
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        visit.action_ai_brief()
        self.assertIn('Penicillin allergy', visit.ai_brief)
        self.assertIn('Joint injection', visit.ai_brief)

    def test_brief_with_provider_is_anonymized(self):
        self._configure_ai()
        past = self._create_visit([{'product_id': self.proc_consult.id}], start=datetime.now() - timedelta(days=30),
                                  note='<p>Ahmed Ali complained of pain on 36. Call him at +218 91 234 5678.</p>')
        past.action_done()
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        sent = {}

        def fake(service, cfg, system, prompt, max_tokens):
            sent['prompt'] = prompt
            sent['system'] = system
            return "- {patient} had a check-up last month\n- **Filling** on 36 today"

        with patch(SERVICE, fake):
            visit.action_ai_brief()
        self.assertNotIn('Ahmed', sent['prompt'])
        self.assertNotIn('234 5678', sent['prompt'])
        self.assertNotIn('Penicillin', sent['prompt'], "medical alerts never leave Odoo")
        self.assertIn('{patient}', sent['prompt'])
        self.assertIn('Ahmed Ali had a check-up', visit.ai_brief)
        self.assertIn('<strong>Filling</strong>', visit.ai_brief)
        self.assertIn('Penicillin allergy', visit.ai_brief, "alerts stay in the local header")
        log = self.env['medical.ai.log'].search([], limit=1)
        self.assertEqual((log.feature, log.state), ('brief', 'ok'))
        self.assertFalse(log.prompt, "prompts are not stored by default")

    # ------------------------------------------------------------ follow-up
    def test_followup_message_and_whatsapp(self):
        plan = self.env['medical.treatment.plan'].create({
            'patient_id': self.patient.id, 'doctor_id': self.doctor.id,
            'line_ids': [(0, 0, {'product_id': self.proc_main.id, 'laterality': 'right'})]})
        plan.action_present()
        plan.action_accept()
        self.assertEqual(plan.unscheduled_amount, 1000.0)
        plan.action_draft_followup()  # template, no provider
        self.assertIn('Ahmed', plan.followup_message)
        self.assertIn('Joint injection', plan.followup_message)
        self._configure_ai()
        with patch(SERVICE, lambda *a, **k: "Dear {patient}, {clinic} is waiting for you."):
            plan.action_draft_followup()
        self.assertEqual(plan.followup_message, f"Dear Ahmed, {self.env.company.name} is waiting for you.")
        action = plan.action_open_whatsapp()
        self.assertTrue(action['url'].startswith('https://wa.me/218912345678?text='))
        visit = self._create_visit()
        plan.line_ids.visit_id = visit
        self.assertEqual(plan.unscheduled_amount, 0.0)

    # ------------------------------------------------------------ appeal
    def test_appeal_letter(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        visit.action_done()
        claim = self.env['medical.claim']._create_batches(False, fields.Date.today())
        claim.action_submit()
        claim.line_ids.write({'approved': 0.0, 'reject_reason': 'Not medically necessary'})
        claim.action_settle()
        line = claim.line_ids
        line.action_draft_appeal()
        self.assertIn('M-0001', line.appeal_text)
        self.assertIn('Not medically necessary', line.appeal_text)
        self._configure_ai()
        with patch(SERVICE, lambda *a, **k: "Re: {patient} ({member_no}). Signed {doctor}"):
            line.action_draft_appeal()
        self.assertEqual(line.appeal_text, "Re: Ahmed Ali (M-0001). Signed Dr. Mona")

    def test_provider_error_is_logged(self):
        import requests
        self._configure_ai()

        def boom(*args, **kwargs):
            raise requests.exceptions.ConnectionError("no route")

        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        with patch(SERVICE, boom):
            try:
                visit.action_ai_brief()
            except UserError:
                pass
            else:
                self.fail("a provider error must surface as a UserError")
        self.assertEqual(self.env['medical.ai.log'].search([], limit=1).state, 'error')

    def test_markdown_rendering(self):
        html = self.env['medical.ai.service']._text_to_html("Intro <b>x</b>\n- one **bold**\n- two")
        self.assertIn('&lt;b&gt;', html)
        self.assertIn('<li>one <strong>bold</strong></li>', html)
