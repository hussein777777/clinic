from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.medical_core.tests.common import MedicalCommon


@tagged('post_install', '-at_install', 'medical')
class TestObgyn(MedicalCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.obgyn = cls.env.ref('medical_obgyn.dept_obgyn')
        cls.obstetrician = cls.env['hr.employee'].create({
            'name': 'Dr. Nadia (obgyn)', 'is_practitioner': True, 'medical_department_ids': [(6, 0, cls.obgyn.ids)]})
        cls.mother = cls.env['res.partner'].create({'name': 'Fatma Salem', 'is_patient': True, 'gender': 'female',
                                                    'birthdate': '1994-05-01'})

    def _pregnancy(self, weeks=20, **kw):
        lmp = fields.Date.today() - timedelta(weeks=weeks)
        return self.env['medical.pregnancy'].create(dict({
            'patient_id': self.mother.id, 'doctor_id': self.obstetrician.id, 'lmp': lmp, 'gravida': 2, 'para': 1}, **kw))

    def test_dates_and_contacts(self):
        preg = self._pregnancy(weeks=20)
        self.assertEqual(preg.edd, preg.lmp + timedelta(days=280))
        self.assertEqual(preg.ga_label, '20w 0d')
        self.assertEqual(preg.trimester, '2')
        self.assertEqual(len(preg.contact_ids), 8)
        self.assertEqual(preg.contact_ids.filtered(lambda c: c.week == 12).due_date, preg.lmp + timedelta(weeks=12))
        self.assertEqual(preg.risk_level, 'low')
        preg.risk_factor_ids = self.env.ref('medical_obgyn.risk_00')
        self.assertEqual(preg.risk_level, 'high')
        # dating scan moves the EDD and replans the contacts
        self.env['medical.obstetric.us'].create({'pregnancy_id': preg.id, 'ga_weeks': 21, 'ga_extra_days': 0,
                                                 'use_for_dating': True})
        self.assertEqual(preg.edd, fields.Date.today() + timedelta(days=280 - 147))
        self.assertEqual(preg.ga_label, '21w 0d')

    def test_antenatal_visit_alerts_and_contact(self):
        preg = self._pregnancy(weeks=30)
        contact = preg.contact_ids.filtered(lambda c: c.week == 30)
        visit = self.env['medical.visit'].browse(contact.action_book()['res_id'])
        self.assertEqual(visit.pregnancy_id, preg)
        self.assertEqual(visit.department_id, self.obgyn)
        visit.write({'bp_systolic': 150, 'bp_diastolic': 95, 'urine_protein': '1', 'fetal_heart_rate': 172,
                     'fundal_height': 30})
        self.assertIn('140/90', visit.obstetric_alert)
        self.assertIn('pre-eclampsia', visit.obstetric_alert)
        self.assertIn('110-160', visit.obstetric_alert)
        fhr = self.env['medical.observation'].search([('visit_id', '=', visit.id), ('type_id.code', '=', 'fhr')])
        self.assertEqual(fhr.flag, 'high')
        visit.room_id = False
        visit.action_done()
        self.assertEqual(contact.state, 'done')
        facts = dict(visit._brief_specialty_facts())
        self.assertIn('EDD', facts['Pregnancy'][0])

    def test_delivery_and_baby(self):
        preg = self._pregnancy(weeks=39)
        with self.assertRaises(UserError):
            preg.action_delivered()
        preg.write({'delivery_date': fields.Date.today(), 'delivery_mode': 'svd', 'birth_weight_g': 3250,
                    'apgar_1': 8, 'apgar_5': 9})
        preg.action_delivered()
        self.assertEqual(preg.state, 'delivered')
        self.assertFalse(preg.contact_ids.filtered(lambda c: c.state == 'planned'))
        preg.action_register_baby()
        self.assertTrue(preg.baby_patient_id.is_patient)
        self.assertEqual(preg.baby_patient_id.birthdate, fields.Date.today())

    def test_epds_alert(self):
        epds = self.env['medical.assessment'].create({
            'template_id': self.env.ref('medical_obgyn.score_epds').id, 'patient_id': self.mother.id})
        for answer in epds.answer_ids:
            answer.option_id = answer.item_id.option_ids.sorted('score')[0]
        self.assertEqual(epds.score, 0)
        self.assertFalse(epds.has_alert)
        last = epds.answer_ids.sorted('sequence')[-1]
        last.option_id = last.item_id.option_ids.filtered(lambda o: o.score == 1)
        self.assertTrue(epds.has_alert)
