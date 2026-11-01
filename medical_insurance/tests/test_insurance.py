from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MedicalInsuranceCommon


@tagged('post_install', '-at_install', 'medical')
class TestMedicalInsurance(MedicalInsuranceCommon):

    def test_rule_matching(self):
        self.assertEqual(self.ins_plan._match_rule(self.proc_main).coverage_pct, 80.0)
        self.assertEqual(self.ins_plan._match_rule(self.proc_consult).coverage_pct, 100.0)

    def test_split_and_claim_line(self):
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        self.assertEqual(visit.policy_id, self.policy)
        line = visit.line_ids
        self.assertEqual(line.insurance_amount, 800.0)
        self.assertEqual(line.patient_amount, 200.0)
        visit.action_done()
        claim_line = line.claim_line_ids
        self.assertEqual(claim_line.claimed, 800.0)
        self.assertEqual(claim_line.state, 'to_claim')
        self.assertEqual(self.policy.used_amount, 800.0)
        self.assertEqual(self.policy.remaining_limit, 4200.0)

    def test_annual_limit_caps_the_visit(self):
        self.ins_plan.annual_limit = 1000.0
        visit = self._create_visit([
            {'product_id': self.proc_main.id, 'laterality': 'right'},
            {'product_id': self.proc_main.id, 'laterality': 'left'},
        ])
        visit.action_done()
        self.assertEqual(sum(visit.line_ids.claim_line_ids.mapped('claimed')), 1000.0)
        self.assertEqual(visit.amount_patient, 1000.0)

    def test_preauth_required_and_approved(self):
        plan = self.env['medical.treatment.plan'].create({
            'patient_id': self.patient.id,
            'doctor_id': self.doctor.id,
            'line_ids': [(0, 0, {'product_id': self.proc_major.id, 'laterality': 'right'})],
        })
        self.assertEqual(plan.policy_id, self.policy)
        line = plan.line_ids
        self.assertTrue(line.requires_preauth)
        self.assertEqual(line.insurance_amount, 2000.0)
        plan.action_present()
        plan.action_accept()
        visit = self._create_visit()
        line.visit_id = visit
        with self.assertRaises(UserError):
            visit.action_done()

        action = plan.action_request_preauth()
        preauth = self.env['medical.preauth'].browse(action['res_id'])
        preauth.action_send()
        preauth.line_ids.approved_amount = 1500.0
        preauth.approval_no = 'APP-77'
        preauth.action_record_response()
        self.assertEqual(preauth.state, 'partial')
        self.assertEqual(line.insurance_amount, 1500.0)
        self.assertEqual(line.patient_amount, 2500.0)
        visit.action_done()
        self.assertEqual(line.claim_line_ids.approval_no, 'APP-77')

    def test_convert_to_cash(self):
        visit = self._create_visit([{'product_id': self.proc_major.id, 'laterality': 'right'}])
        visit.line_ids.action_convert_to_cash()
        self.assertEqual(visit.line_ids.insurance_amount, 0.0)
        visit.action_done()
        self.assertFalse(visit.line_ids.claim_line_ids)

    def test_claim_batch_settlement_and_resubmission(self):
        visit = self._create_visit([
            {'product_id': self.proc_main.id, 'laterality': 'right'},
            {'product_id': self.proc_consult.id},
        ])
        visit.action_done()
        service_date = visit.line_ids[0].date_executed
        claims = self.env['medical.claim']._create_batches(service_date, service_date)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims.amount_claimed, 800.0 + 300.0)
        claims.action_submit()
        self.assertEqual(set(claims.line_ids.mapped('state')), {'claimed'})
        filling_cl = claims.line_ids.filtered(lambda l: l.product_id == self.proc_main)
        filling_cl.write({'approved': 500.0, 'reject_reason': 'Tariff'})
        claims.action_settle()
        self.assertEqual(filling_cl.state, 'partially_approved')
        self.assertEqual(claims.amount_rejected, 300.0)
        new_line = filling_cl.action_resubmit()
        self.assertEqual(new_line.claimed, 300.0)
        self.assertEqual(new_line.state, 'to_claim')
        # export works
        action = claims.action_export_xlsx()
        self.assertTrue(claims.export_file)
        self.assertIn('/web/content/medical.claim/', action['url'])

    def test_excluded_specialty(self):
        visit = self._create_visit([{'product_id': self.proc_excluded.id}])
        self.assertEqual(visit.line_ids.insurance_amount, 0.0)

    def test_frequency_limit(self):
        self.ins_plan.rule_ids.filtered(lambda r: r.specialty_id == self.spec_minor).frequency_months = 6
        v1 = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        v1.action_done()
        v2 = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        self.assertEqual(v2.line_ids.insurance_amount, 0.0)

    def test_reports_render(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        visit.action_done()
        claims = self.env['medical.claim']._create_batches(False, visit.line_ids.date_executed)
        html = self.env['ir.actions.report']._render_qweb_html('medical_insurance.report_medical_claim', claims.ids)[0]
        self.assertIn(b'M-0001', html)
