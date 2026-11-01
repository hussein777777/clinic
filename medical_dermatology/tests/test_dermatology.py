from odoo.tests import tagged

from odoo.addons.medical_core.tests.common import MedicalCommon

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='


@tagged('post_install', '-at_install', 'medical')
class TestDermatology(MedicalCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.derm = cls.env.ref('medical_dermatology.dept_dermatology')
        cls.dermatologist = cls.env['hr.employee'].create({
            'name': 'Dr. Hala (derm)', 'is_practitioner': True, 'medical_department_ids': [(6, 0, cls.derm.ids)]})

    def test_lesion_abcde_and_reminder(self):
        lesion = self.env['medical.skin.lesion'].create({
            'patient_id': self.patient.id, 'body_site_id': self.env.ref('medical_core.site_back').id, 'size_mm': 4})
        self.assertEqual(lesion.name, 'Lesion 1')
        self.assertEqual(lesion.risk, 'low')
        self.assertTrue(lesion.activity_ids, "next skin check scheduled")
        lesion.write({'size_mm': 8, 'abcde_border': True})
        self.assertTrue(lesion.abcde_diameter)
        self.assertEqual(lesion.risk, 'watch')
        lesion.abcde_evolving = True
        self.assertEqual(lesion.risk, 'high')
        self.env['medical.skin.photo'].create({'patient_id': self.patient.id, 'lesion_id': lesion.id, 'image': PNG})
        self.assertEqual(lesion.photo_count, 1)
        lesion.status = 'excised'
        self.assertFalse(lesion.next_check)
        self.assertFalse(lesion.activity_ids)

    def test_laser_program_copies_settings(self):
        contract = self.env['medical.care.contract'].create({
            'care_type': 'aesthetic', 'patient_id': self.patient.id, 'doctor_id': self.dermatologist.id,
            'product_id': self.env.ref('medical_dermatology.product_laser_hair_package_6').id,
            'session_product_id': self.env.ref('medical_dermatology.product_laser_hair_session').id,
            'billing_mode': 'sessions', 'session_count': 6, 'est_months': 8})
        contract.action_start()
        first = self.env['medical.visit'].browse(contract.action_new_session_visit()['res_id'])
        self.assertTrue(first.is_derm)
        first.line_ids.action_consent()
        first.line_ids.consent_id.write({'signature': PNG})
        first.line_ids.consent_id.action_sign()
        first.write({'laser_device': 'Diode 810', 'laser_fluence': 18, 'laser_spot_mm': 12, 'skin_reaction': 'erythema'})
        first.action_done()
        second = self.env['medical.visit'].browse(contract.action_new_session_visit()['res_id'])
        second.action_copy_last_settings()
        self.assertEqual(second.laser_fluence, 18)
        self.assertEqual(second.laser_device, 'Diode 810')
        facts = dict(second._brief_specialty_facts())
        self.assertTrue(any('18' in row for row in facts['Skin']))
