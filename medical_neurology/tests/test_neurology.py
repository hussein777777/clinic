from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.medical_core.tests.common import MedicalCommon


@tagged('post_install', '-at_install', 'medical')
class TestNeurology(MedicalCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.neuro = cls.env.ref('medical_neurology.dept_neurology')
        cls.neurologist = cls.env['hr.employee'].create({
            'name': 'Dr. Salma (neuro)', 'is_practitioner': True, 'medical_department_ids': [(6, 0, cls.neuro.ids)]})

    def _visit(self):
        return self._create_visit([{'product_id': self.env.ref('medical_neurology.product_neuro_consultation').id}],
                                  doctor_id=self.neurologist.id, room_id=False)

    def test_focal_deficit_flag(self):
        visit = self._visit()
        self.assertTrue(visit.is_neuro)
        visit.write({'neuro_power_rul': '5', 'neuro_power_lul': '3', 'neuro_power_rll': '5', 'neuro_power_lll': '5',
                     'neuro_plantar_l': 'extensor'})
        self.assertTrue(visit.neuro_focal_deficit)
        self.assertIn('left arm weaker (3/5)', visit.neuro_findings)
        self.assertIn('left Babinski', visit.neuro_findings)
        visit.write({'neuro_power_lul': '5', 'neuro_plantar_l': 'flexor'})
        self.assertFalse(visit.neuro_focal_deficit)

    def test_nihss_and_gcs(self):
        visit = self._visit()
        nihss = self.env.ref('medical_neurology.score_nihss')
        assessment = self.env['medical.assessment'].create({
            'template_id': nihss.id, 'patient_id': self.patient.id, 'visit_id': visit.id})
        self.assertEqual(len(assessment.answer_ids), 15)
        self.assertEqual(assessment.max_score, 42)
        for answer in assessment.answer_ids:
            answer.option_id = answer.item_id.option_ids.sorted('score')[0]
        right_arm = assessment.answer_ids.filtered(lambda a: a.item_id.name.startswith('5b'))
        right_arm.option_id = right_arm.item_id.option_ids.filtered(lambda o: o.score == 3)
        language = assessment.answer_ids.filtered(lambda a: a.item_id.name.startswith('9.'))
        language.option_id = language.item_id.option_ids.filtered(lambda o: o.score == 2)
        self.assertEqual(assessment.score, 5)
        self.assertEqual(assessment.interpretation, 'Moderate stroke')
        assessment.action_save()
        gcs = self.env['medical.assessment'].create({
            'template_id': self.env.ref('medical_neurology.score_gcs').id, 'patient_id': self.patient.id})
        for answer in gcs.answer_ids:
            answer.option_id = answer.item_id.option_ids.sorted('score')[-1]
        self.assertEqual(gcs.score, 15)
        self.assertEqual(gcs.interpretation, 'Mild or none')

    def test_moca_numeric_sections(self):
        moca = self.env['medical.assessment'].create({
            'template_id': self.env.ref('medical_neurology.score_moca').id, 'patient_id': self.patient.id})
        values = [4, 3, 5, 2, 1, 2, 6, 1]
        for answer, value in zip(moca.answer_ids.sorted('sequence'), values):
            answer.number = value
        self.assertEqual(moca.score, 24)
        self.assertEqual(moca.interpretation, 'Mild cognitive impairment')

    def test_seizure_diary_stats(self):
        Event = self.env['medical.neuro.event']
        now = fields.Datetime.now()
        Event.create([
            {'patient_id': self.patient.id, 'date': now - timedelta(days=120), 'seizure_type': 'gtc', 'duration_min': 2},
            {'patient_id': self.patient.id, 'date': now - timedelta(days=40), 'seizure_type': 'focal_impaired'},
            {'patient_id': self.patient.id, 'date': now - timedelta(days=10), 'event_type': 'headache', 'severity': 7},
            {'patient_id': self.patient.id, 'date': now - timedelta(days=9), 'event_type': 'headache', 'severity': 5},
        ])
        self.assertEqual(self.patient.seizures_90d, 1)
        self.assertEqual(self.patient.seizure_free_days, 40)
        self.assertEqual(self.patient.headache_days_30d, 2)
        visit = self._visit()
        facts = dict(visit._brief_specialty_facts())
        self.assertTrue(any('seizure-free for 40 days' in row for row in facts['Seizure / headache diary']))
