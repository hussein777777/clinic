from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.medical_core.tests.common import MedicalCommon


@tagged('post_install', '-at_install', 'medical')
class TestInternalMedicine(MedicalCommon):

    def _observe(self, code, value, days_ago=1):
        return self.env['medical.observation'].create({
            'patient_id': self.patient.id, 'type_id': self.env.ref(f'medical_core.obs_{code}').id, 'value': value,
            'date': fields.Datetime.now() - timedelta(days=days_ago)})

    def test_auto_enrollment_and_control(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        visit.diagnosis_ids = [(0, 0, {'diagnosis_id': self.env.ref('medical_core.icd_e11_9').id, 'is_primary': True})]
        visit.action_done()
        enrollment = self.patient.chronic_enrollment_ids
        self.assertEqual(enrollment.program_id, self.env.ref('medical_internal.program_diabetes'))
        self.assertEqual(enrollment.control_status, 'unknown')
        self.assertIn('HbA1c', enrollment.tests_due)
        self._observe('hba1c', 6.6)
        self.assertEqual(enrollment.control_status, 'controlled')
        self._observe('ldl', 145)
        self.assertEqual(enrollment.control_status, 'partial')
        self.assertIn('✗ LDL Cholesterol', enrollment.control_detail)
        action = enrollment.action_order_due_tests()
        orders = self.env['medical.order'].search(action['domain'])
        self.assertEqual(orders.order_type, 'lab')
        names = orders.line_ids.product_id.mapped('name')
        self.assertNotIn('HbA1c', names, "HbA1c was just done")
        self.assertNotIn('Lipid Profile', names, "an LDL result counts as a lipid profile")
        self.assertIn('Kidney Function (Urea, Creatinine)', names)
        facts = dict(visit._brief_specialty_facts())
        self.assertTrue(facts['Chronic programs'][0].startswith('Diabetes'))

    def test_recall_cron(self):
        enrollment = self.env['medical.chronic.enrollment'].create({
            'patient_id': self.patient.id, 'program_id': self.env.ref('medical_internal.program_hypertension').id,
            'doctor_id': self.doctor.id, 'start_date': fields.Date.today() - timedelta(days=120)})
        self.assertTrue(enrollment.is_review_due)
        self.assertIn(enrollment, self.env['medical.chronic.enrollment'].search([('has_tests_due', '=', True)]))
        self.env['medical.chronic.enrollment']._cron_chronic_recall()
        self.assertEqual(len(enrollment.activity_ids), 1)
        self.env['medical.chronic.enrollment']._cron_chronic_recall()
        self.assertEqual(len(enrollment.activity_ids), 1, "not duplicated")
        enrollment.action_mark_reviewed()
        self.assertFalse(enrollment.is_review_due)
        self.assertFalse(enrollment.activity_ids)

    def test_phq9_safety_alert(self):
        phq = self.env['medical.assessment'].create({
            'template_id': self.env.ref('medical_internal.score_phq9').id, 'patient_id': self.patient.id})
        for answer in phq.answer_ids:
            answer.option_id = answer.item_id.option_ids.filtered(lambda o: o.score == 1)
        self.assertEqual(phq.score, 9)
        self.assertEqual(phq.interpretation, 'Mild')
        self.assertTrue(phq.has_alert)
        self.assertIn('Thoughts that you would be better off dead', phq.alert_text)
