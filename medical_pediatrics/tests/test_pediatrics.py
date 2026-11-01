from datetime import date, datetime, timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.medical_core.tests.common import MedicalCommon


@tagged('post_install', '-at_install', 'medical')
class TestPediatrics(MedicalCommon):

    def _baby(self, months=12, gender='male', **kw):
        birth = fields.Date.today() - timedelta(days=round(months * 30.4375))
        return self.env['res.partner'].create(dict({'name': 'Baby Ali', 'is_patient': True, 'gender': gender,
                                                    'birthdate': birth}, **kw))

    def test_who_zscores(self):
        Ref = self.env['medical.growth.reference']
        self.assertEqual(Ref.search_count([]), 488)
        lms = Ref._lms('wfa', 'male', 12)
        self.assertAlmostEqual(Ref._zscore('wfa', 'male', 12, lms[1]), 0.0, places=6)
        # WHO table: boys 12 months, weight-for-age -2 SD = 7.7 kg, +2 SD = 12.0 kg
        self.assertAlmostEqual(Ref._zscore('wfa', 'male', 12, 7.7), -2.0, delta=0.06)
        self.assertAlmostEqual(Ref._zscore('wfa', 'male', 12, 12.0), 2.0, delta=0.06)
        self.assertEqual(Ref._classify('wfa', -2.5), 'Underweight')
        self.assertEqual(Ref._classify('bmifa', 2.4), 'Overweight')
        self.assertIsNone(Ref._zscore('wfa', 'male', 70, 20))
        # interpolation between months stays between neighbours
        z = Ref._zscore('lhfa', 'female', 6.5, 66)
        self.assertTrue(-1 < z < 1)

    def test_growth_payload_and_alerts(self):
        baby = self._baby(months=12)
        visit = self._create_visit([{'product_id': self.proc_consult.id}], patient_id=baby.id, room_id=False)
        visit.write({'weight': 7.2, 'height': 75.0, 'head_circumference': 46.0})
        self.assertTrue(visit.is_pediatric)
        data = baby.get_growth_data()
        self.assertTrue(data['ready'])
        wfa = next(i for i in data['indicators'] if i['key'] == 'wfa')
        self.assertEqual(len(wfa['points']), 1)
        self.assertLess(wfa['points'][0]['z'], -2)
        self.assertEqual(wfa['points'][0]['status'], 'Underweight')
        self.assertEqual(len(wfa['curves']['50']), 61)
        hc = next(i for i in data['indicators'] if i['key'] == 'hcfa')
        self.assertEqual(hc['points'][0]['value'], 46.0)
        alerts = visit._growth_alerts()
        self.assertTrue(any('Underweight' in a for a in alerts))
        self.assertFalse(self.patient.get_growth_data()['ready'] and
                         any(i['points'] for i in self.patient.get_growth_data()['indicators']))

    def test_vaccination_plan_and_give(self):
        baby = self._baby(months=3)
        self.assertEqual(len(baby.vaccination_ids), self.env['medical.vaccine.schedule'].search_count([]),
                         "plan generated automatically for a new child")
        bcg = baby.vaccination_ids.filtered(lambda v: v.vaccine_id.code == 'BCG')
        self.assertEqual(bcg.due_date, baby.birthdate)
        self.assertTrue(bcg.is_overdue)
        self.assertTrue(baby.vaccines_overdue >= 7)
        visit = self._create_visit([{'product_id': self.proc_consult.id}], patient_id=baby.id, room_id=False)
        visit.action_check_in()
        bcg.write({'lot_number': 'BCG-2209', 'site': 'left_arm'})
        bcg.action_give()
        self.assertEqual(bcg.state, 'given')
        self.assertEqual(bcg.visit_id, visit)
        self.assertIn('Vaccine: BCG', visit.line_ids.product_id.mapped('name'))
        self.env['medical.vaccination']._cron_vaccination_reminders()
        due = baby.vaccination_ids.filtered(lambda v: v.state == 'planned' and v.due_date <= fields.Date.today())
        self.assertTrue(all(v.activity_ids for v in due))
        baby.action_generate_vaccination_plan()
        self.assertEqual(len(baby.vaccination_ids), self.env['medical.vaccine.schedule'].search_count([]), "no duplicates")

    def test_weight_based_dose(self):
        baby = self._baby(months=24)
        prescription = self.env['medical.prescription'].create({
            'patient_id': baby.id, 'doctor_id': self.doctor.id, 'patient_weight': 12.0,
            'line_ids': [(0, 0, {'product_id': self.env.ref('medical_core.medicine_paracetamol_syrup').id,
                                 'frequency': 'q6h', 'duration_days': 3})],
        })
        line = prescription.line_ids
        self.assertEqual(line.dose_mg_kg, 15)
        self.assertEqual(line.dose_mg, 180)
        self.assertEqual(line.dose_ml, 7.5)
        self.assertEqual(line.dose, '7.5 ml (180 mg)')
        prescription.patient_weight = 80
        self.assertTrue(line.dose_capped)
        self.assertEqual(line.dose_mg, 1000)

    def test_milestones(self):
        baby = self._baby(months=20)
        baby.action_update_milestones()
        checks = baby.milestone_check_ids
        self.assertTrue(checks)
        self.assertTrue(all(c.age_months <= 23 for c in checks))
        sits = checks.filtered(lambda c: c.milestone_id.name == 'Sits without support')
        self.assertEqual(sits.status, 'late')
        sits.achieved = True
        self.assertEqual(sits.status, 'done')
        self.assertTrue(baby.milestones_late > 0)
