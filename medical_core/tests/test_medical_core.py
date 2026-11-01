from datetime import datetime

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import MedicalCommon


@tagged('post_install', '-at_install', 'medical')
class TestMedicalCore(MedicalCommon):

    def test_patient_code_and_department(self):
        self.assertTrue(self.patient.patient_code.startswith('P'))
        self.assertTrue(self.patient.patient_age_label)
        visit = self._create_visit()
        self.assertEqual(visit.department_id, self.department, "the doctor's clinic is used")

    def test_plan_to_visit_execution(self):
        plan = self.env['medical.treatment.plan'].create({
            'patient_id': self.patient.id,
            'doctor_id': self.doctor.id,
            'line_ids': [(0, 0, {'product_id': self.proc_main.id, 'laterality': 'right'}),
                         (0, 0, {'product_id': self.proc_consult.id})],
        })
        self.assertEqual(plan.department_id, self.department)
        self.assertEqual(plan.amount_total, 1300.0)
        self.assertEqual(plan.line_ids[0].site_label, 'Right')
        plan.action_present()
        plan.action_accept()
        visit = self._create_visit()
        wiz = self.env['medical.visit.add.lines'].create({'visit_id': visit.id, 'line_ids': [(6, 0, plan.line_ids.ids)]})
        wiz.action_confirm()
        self.assertEqual(set(plan.line_ids.mapped('state')), {'scheduled'})
        visit.action_check_in()
        visit.action_done()
        self.assertIn(visit.state, ('done', 'invoiced'))  # 'invoiced' when medical_account is installed
        self.assertEqual(plan.state, 'done')

    def test_site_required(self):
        visit = self._create_visit([{'product_id': self.proc_main.id}])
        with self.assertRaises(UserError):
            visit.action_done()
        knee = self.env.ref('medical_core.site_knee')
        self.proc_main.site_requirement = 'body_site'
        visit.line_ids.body_site_id = knee
        with self.assertRaises(UserError, msg="the knee has sides"):
            visit.action_done()
        visit.line_ids.laterality = 'left'
        visit.action_done()
        self.assertEqual(visit.line_ids.site_label, 'Knee Left')

    def test_room_double_booking(self):
        start = datetime(2026, 10, 10, 10, 0)
        self._create_visit(start=start, duration=1.0)
        with self.assertRaises(ValidationError):
            self._create_visit(start=datetime(2026, 10, 10, 10, 30), duration=0.5)

    def test_executed_line_is_locked(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        visit.action_done()
        with self.assertRaises(UserError):
            visit.line_ids.price_unit = 1

    def test_consent_required(self):
        self.proc_major.requires_consent = True
        visit = self._create_visit([{'product_id': self.proc_major.id}])
        with self.assertRaises(UserError):
            visit.action_done()
        visit.line_ids.action_consent()
        consent = visit.line_ids.consent_id
        self.assertIn('Arthroscopy', consent.consent_text)
        with self.assertRaises(UserError):
            consent.action_sign()
        consent.signature = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
        consent.action_sign()
        self.assertTrue(consent.signed)
        visit.action_done()

    def test_follow_up_booking(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}], start=datetime(2026, 10, 10, 9, 0))
        visit.write({'follow_up_in': 2, 'follow_up_unit': 'weeks'})
        self.assertEqual(str(visit.follow_up_date), '2026-10-24')
        visit.action_done()
        visit.action_book_follow_up()
        follow = visit.follow_up_visit_id
        self.assertEqual(follow.visit_type, 'follow_up')
        self.assertEqual(follow.start.date().isoformat(), '2026-10-24')
        self.assertEqual(follow.line_ids.product_id, self.env.ref('medical_core.product_follow_up'))
        visit.action_book_follow_up()
        self.assertEqual(self.env['medical.visit'].search_count([('patient_id', '=', self.patient.id)]), 2)
