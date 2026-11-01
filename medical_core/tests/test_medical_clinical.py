from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import MedicalCommon


@tagged('post_install', '-at_install', 'medical')
class TestMedicalClinical(MedicalCommon):

    def test_vitals_become_measurements(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        visit.write({'bp_systolic': 165, 'bp_diastolic': 95, 'pulse': 72, 'weight': 80.0, 'height': 175.0})
        self.assertAlmostEqual(visit.bmi, 26.1, places=1)
        self.assertIn('165', visit.vitals_warning)
        obs = self.env['medical.observation'].search([('visit_id', '=', visit.id)])
        by_code = {o.type_id.code: o for o in obs}
        self.assertEqual(by_code['bp_systolic'].flag, 'high')
        self.assertEqual(by_code['pulse'].flag, 'normal')
        self.assertIn('bmi', by_code)
        visit.pulse = 0
        self.assertNotIn('pulse', self.env['medical.observation'].search([('visit_id', '=', visit.id)]).type_id.mapped('code'))
        trend = self.env['medical.observation'].get_trend(self.patient.id)
        systolic = next(s for s in trend if s['code'] == 'bp_systolic')
        self.assertEqual(systolic['points'][-1]['value'], 165)
        self.assertEqual(systolic['normal_max'], 139)
        with self.assertRaises(ValidationError):
            visit.pain_score = 12

    def test_chronic_diagnosis_goes_to_problem_list(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        htn = self.env.ref('medical_core.icd_i10')
        uri = self.env.ref('medical_core.icd_j06_9')
        visit.diagnosis_ids = [(0, 0, {'diagnosis_id': htn.id, 'is_primary': True}),
                               (0, 0, {'diagnosis_id': uri.id})]
        self.assertEqual(visit.primary_diagnosis_id, htn)
        visit.action_done()
        self.assertEqual(self.patient.problem_ids.diagnosis_id, htn, "only the chronic diagnosis is kept")
        visit2 = self._create_visit([{'product_id': self.proc_consult.id}])
        visit2.diagnosis_ids = [(0, 0, {'diagnosis_id': htn.id})]
        visit2.action_done()
        self.assertEqual(len(self.patient.problem_ids), 1, "not duplicated")

    def test_prescription_allergy_check(self):
        penicillin = self.env.ref('medical_core.allergen_penicillin')
        self.patient.allergen_ids = penicillin
        self.assertTrue(self.patient.has_medical_alert)
        visit = self._create_visit([{'product_id': self.proc_consult.id}], weight=70)
        action = visit.action_new_prescription()
        prescription = self.env['medical.prescription'].browse(action['res_id'])
        self.assertEqual(prescription.patient_weight, 70)
        prescription.line_ids = [(0, 0, {'product_id': self.env.ref('medical_core.medicine_amoxicillin_500').id,
                                         'dose': '1 capsule', 'frequency': 'tid', 'duration_days': 7})]
        line = prescription.line_ids
        self.assertIn('Amoxicillin', line.name)
        self.assertEqual(line.route, 'oral')
        self.assertIn('Penicillins', prescription.allergy_warning)
        with self.assertRaises(UserError):
            prescription.action_issue()
        prescription.action_issue_override()
        self.assertEqual(prescription.state, 'done')
        self.assertIn('7', line._sig())
        self.assertEqual(line._administrations(), 21)

    def test_lab_order_billing_and_results(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        cbc = self.env.ref('medical_core.product_lab_cbc')
        order = self.env['medical.order'].create({
            'patient_id': self.patient.id, 'visit_id': visit.id, 'doctor_id': self.doctor.id,
            'line_ids': [(0, 0, {'product_id': cbc.id})],
        })
        self.assertEqual(order.department_id, self.department)
        self.assertEqual(len(order.line_ids.result_ids), 3, "Hb, WBC, platelets")
        order.action_request()
        charge = order.line_ids.treatment_line_id
        self.assertEqual(charge.visit_id, visit, "billed on the open visit")
        self.assertEqual(charge.price_unit, cbc.lst_price)
        with self.assertRaises(UserError):
            order.action_done()
        hb = order.line_ids.result_ids.filtered(lambda r: r.type_id.code == 'hb')
        hb.value = 9.1
        order.line_ids.result_ids.filtered(lambda r: r.type_id.code == 'wbc').value = 7.0
        order.action_done()
        self.assertEqual(hb.flag, 'low')
        self.assertTrue(order.has_abnormal)
        self.assertEqual(len(order.line_ids.result_ids.filtered('observation_id')), 2, "platelets not measured")
        self.assertTrue(order.activity_ids, "the doctor is warned about the abnormal result")
        # after the visit is closed, a new order gets its own test visit
        visit.action_done()
        order2 = self.env['medical.order'].create({
            'patient_id': self.patient.id, 'visit_id': visit.id, 'doctor_id': self.doctor.id,
            'line_ids': [(0, 0, {'product_id': self.env.ref('medical_core.product_lab_hba1c').id})],
        })
        order2.action_request()
        self.assertNotEqual(order2.line_ids.treatment_line_id.visit_id, visit)
        self.assertEqual(order2.line_ids.treatment_line_id.visit_id.state, 'checked_in')

    def test_external_order_is_not_billed(self):
        order = self.env['medical.order'].create({
            'patient_id': self.patient.id, 'doctor_id': self.doctor.id, 'performer': 'external',
            'external_partner_id': self.env['res.partner'].create({'name': 'Outside Lab'}).id,
            'order_type': 'imaging',
            'line_ids': [(0, 0, {'product_id': self.env.ref('medical_core.product_img_mri').id,
                                 'body_site_id': self.env.ref('medical_core.site_knee').id, 'laterality': 'left'})],
        })
        order.action_request()
        self.assertFalse(order.line_ids.treatment_line_id)
        self.assertEqual(order.line_ids.display_name, 'MRI Knee Left')
        order.conclusion = '<p>Medial meniscus tear.</p>'
        order.action_done()
        self.assertEqual(order.state, 'done')

    def test_referral_booking(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        other = self.env['medical.department'].create({'name': 'Other Clinic', 'code': 'OTH'})
        specialist = self.env['hr.employee'].create({'name': 'Dr. Omar', 'is_practitioner': True,
                                                     'medical_department_ids': [(6, 0, other.ids)]})
        visit.action_refer()
        referral = self.env['medical.referral'].create({
            'patient_id': self.patient.id, 'visit_id': visit.id, 'from_doctor_id': self.doctor.id,
            'to_department_id': other.id, 'reason': 'Knee pain for MRI review',
        })
        referral.action_book()
        self.assertEqual(referral.state, 'booked')
        self.assertEqual(referral.booked_visit_id.doctor_id, specialist)
        self.assertEqual(referral.booked_visit_id.department_id, other)

    def test_assessment_scoring(self):
        Template = self.env['medical.assessment.template']
        template = Template.create({
            'name': 'Test score', 'code': 'T-SCORE',
            'observation_type_id': self.env['medical.observation.type'].create(
                {'name': 'Test score', 'code': 't_score', 'category': 'score'}).id,
            'item_ids': [
                (0, 0, {'name': 'Q1', 'option_ids': [(0, 0, {'name': 'No', 'score': 0}), (0, 0, {'name': 'Yes', 'score': 2})]}),
                (0, 0, {'name': 'Q2', 'answer_type': 'number', 'min_value': 0, 'max_value': 5}),
            ],
            'range_ids': [(0, 0, {'min_score': 0, 'max_score': 3, 'name': 'Low', 'severity': 'mild'}),
                          (0, 0, {'min_score': 3.01, 'max_score': 7, 'name': 'High', 'severity': 'severe'})],
        })
        visit = self._create_visit()
        assessment = self.env['medical.assessment'].create({
            'template_id': template.id, 'patient_id': self.patient.id, 'visit_id': visit.id})
        self.assertEqual(len(assessment.answer_ids), 2)
        q1 = assessment.answer_ids.filtered(lambda a: a.item_id.name == 'Q1')
        q2 = assessment.answer_ids - q1
        with self.assertRaises(UserError):
            assessment.action_save()
        q1.option_id = q1.item_id.option_ids.filtered(lambda o: o.score == 2)
        q2.number = 4
        self.assertEqual(assessment.score, 6)
        self.assertEqual(assessment.max_score, 7)
        self.assertEqual(assessment.interpretation, 'High')
        assessment.action_save()
        self.assertEqual(assessment.observation_id.value, 6)
        with self.assertRaises(ValidationError):
            q2.number = 9

    def test_care_program_sessions(self):
        Product = self.env['product.product']
        common = {'type': 'service', 'is_medical_procedure': True, 'specialty_id': self.spec_minor.id}
        session = Product.create(dict(common, name='Therapy session (test)', procedure_type='session', list_price=300))
        package = Product.create(dict(common, name='3 sessions package (test)', procedure_type='package', list_price=900))
        contract = self.env['medical.care.contract'].create({
            'patient_id': self.patient.id, 'doctor_id': self.doctor.id, 'product_id': package.id,
            'billing_mode': 'sessions', 'session_count': 3, 'session_product_id': session.id,
        })
        self.assertEqual(contract.package_price, 900.0)
        self.assertEqual(contract.down_payment, 900.0, "paid upfront without installments")
        with self.assertRaises(UserError):
            contract.action_new_session_visit()
        contract.action_start()
        for i in range(3):
            action = contract.action_new_session_visit()
            visit = self.env['medical.visit'].browse(action['res_id'])
            self.assertEqual(visit.line_ids.price_unit, 0.0, "covered by the program")
            visit.room_id = False
            visit.action_done()
        self.assertEqual(contract.sessions_used, 3)
        self.assertEqual(contract.progress, 100.0)
        with self.assertRaises(UserError):
            contract.action_new_session_visit()
        with self.assertRaises(ValidationError):
            self.env['medical.care.contract'].create({
                'patient_id': self.patient.id, 'doctor_id': self.doctor.id, 'product_id': package.id,
                'billing_mode': 'sessions', 'session_count': 0})
