from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.medical_core.tests.common import MedicalCommon

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='


@tagged('post_install', '-at_install', 'medical')
class TestOrthopedics(MedicalCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ortho = cls.env.ref('medical_orthopedics.dept_orthopedics')
        cls.orthopedist = cls.env['hr.employee'].create({
            'name': 'Dr. Omar (ortho)', 'is_practitioner': True, 'medical_department_ids': [(6, 0, cls.ortho.ids)]})
        cls.wrist = cls.env.ref('medical_core.site_wrist')
        cls.knee = cls.env.ref('medical_core.site_knee')

    def _case(self, **kw):
        vals = {'patient_id': self.patient.id, 'doctor_id': self.orthopedist.id, 'case_type': 'fracture',
                'body_site_id': self.wrist.id, 'laterality': 'left',
                'diagnosis_id': self.env.ref('medical_orthopedics.icd_s52_5').id}
        vals.update(kw)
        return self.env['medical.orthopedic.case'].create(vals)

    def test_fracture_case_cast_and_reminder(self):
        case = self._case(injury_date=fields.Date.today() - timedelta(days=60))
        self.assertTrue(case.name.startswith('ORT/'))
        self.assertIn('Wrist', case.display_name)
        self.assertEqual(case.weeks_since_injury, 8)
        action = case.action_apply_cast()
        cast = self.env['medical.orthopedic.cast'].with_context(action['context']).create({
            'cast_type': 'fiberglass', 'removal_due': fields.Date.today() - timedelta(days=3),
            'applied_date': fields.Date.today() - timedelta(days=45)})
        self.assertEqual(cast.case_id, case)
        self.assertEqual(cast.laterality, 'left')
        self.assertEqual(case.state, 'healing', "a cast moves the case to healing")
        self.assertTrue(cast.is_overdue)
        self.assertIn(cast, self.env['medical.orthopedic.cast'].search([('is_overdue', '=', True)]))
        self.assertTrue(cast.activity_ids, "removal reminder")
        self.assertEqual(case.current_cast_id, cast)
        with self.assertRaises(UserError):
            case.action_close()
        cast.action_remove()
        self.assertFalse(cast.activity_ids)
        self.assertFalse(cast.is_overdue)
        case.action_close()
        self.assertEqual(case.state, 'closed')
        self.assertTrue(case.healed_date)

    def test_imaging_order_from_case(self):
        case = self._case()
        action = case.action_order_imaging()
        order = self.env['medical.order'].browse(action['res_id'])
        self.assertEqual(order.orthopedic_case_id, case)
        self.assertEqual(order.line_ids.body_site_id, self.wrist)
        self.assertEqual(order.line_ids.laterality, 'left')

    def test_rom_by_side_and_lysholm(self):
        visit = self._create_visit([{'product_id': self.env.ref('medical_orthopedics.product_ortho_consultation').id}],
                                   doctor_id=self.orthopedist.id, room_id=False)
        flexion = self.env.ref('medical_orthopedics.obs_rom_knee_flexion')
        visit.exam_observation_ids = [
            (0, 0, {'patient_id': self.patient.id, 'type_id': flexion.id, 'laterality': 'right', 'value': 95, 'source': 'exam'}),
            (0, 0, {'patient_id': self.patient.id, 'type_id': flexion.id, 'laterality': 'left', 'value': 140, 'source': 'exam'}),
        ]
        right, left = visit.exam_observation_ids.sorted(lambda o: o.laterality != 'right')
        self.assertEqual(right.flag, 'low')
        self.assertEqual(left.flag, 'normal')
        trend = self.env['medical.observation'].get_trend(self.patient.id)
        names = [s['name'] for s in trend if s['code'] == 'rom_knee_flexion']
        self.assertEqual(sorted(names), ['Knee Flexion (Left)', 'Knee Flexion (Right)'])
        lysholm = self.env.ref('medical_orthopedics.score_lysholm')
        assessment = self.env['medical.assessment'].create({
            'template_id': lysholm.id, 'patient_id': self.patient.id, 'visit_id': visit.id})
        for answer in assessment.answer_ids:
            answer.option_id = answer.item_id.option_ids.sorted('score')[-1]
        self.assertEqual(assessment.score, 100)
        self.assertEqual(assessment.interpretation, 'Excellent')
        assessment.action_save()
        self.assertEqual(assessment.observation_id.type_id.code, 'lysholm')

    def test_oswestry_percentage(self):
        odi = self.env.ref('medical_orthopedics.score_odi')
        assessment = self.env['medical.assessment'].create({'template_id': odi.id, 'patient_id': self.patient.id})
        self.assertEqual(len(assessment.answer_ids), 10)
        for answer in assessment.answer_ids:
            answer.option_id = answer.item_id.option_ids.filtered(lambda o: o.score == 2)
        self.assertEqual(assessment.score, 40.0)
        self.assertEqual(assessment.interpretation, 'Moderate disability')

    def test_physio_program_sessions_and_pain(self):
        case = self._case(case_type='sprain', body_site_id=self.knee.id, laterality='right')
        action = case.action_start_physio()
        contract = self.env['medical.care.contract'].browse(action['res_id'])
        self.assertEqual(case.state, 'rehab')
        self.assertEqual(contract.care_type, 'physiotherapy')
        self.assertEqual(contract.session_count, 10)
        contract.action_start()
        visit = self.env['medical.visit'].browse(contract.action_new_session_visit()['res_id'])
        self.assertTrue(visit.is_physio)
        visit.write({'pain_score': 7, 'pain_after': 4,
                     'physio_modality_ids': [(4, self.env.ref('medical_orthopedics.modality_2').id)]})
        visit.action_done()
        self.assertEqual(contract.sessions_used, 1)
        pains = self.env['medical.observation'].search([('visit_id', '=', visit.id), ('type_id.code', '=', 'pain')])
        self.assertEqual(sorted(pains.mapped('value')), [4.0, 7.0])

    def test_surgery_checklist_from_case(self):
        case = self._case(case_type='ligament', body_site_id=self.knee.id, laterality='right')
        acl = self.env.ref('medical_orthopedics.product_acl_reconstruction')
        theatre = self.env.ref('medical_core.room_theatre_1')
        surgery = self.env['medical.surgery'].with_context(case.action_book_surgery()['context']).create({
            'room_id': theatre.id, 'scheduled_start': datetime(2026, 11, 2, 8, 0), 'duration': 2.0})
        self.assertEqual(surgery.orthopedic_case_id, case)
        self.assertEqual(surgery.visit_id.room_id, theatre)
        with self.assertRaises(UserError):
            surgery.action_schedule()
        surgery.line_ids = [(0, 0, {'product_id': acl.id, 'laterality': 'right', 'visit_id': surgery.visit_id.id})]
        surgery.action_schedule()
        self.assertEqual(surgery.state, 'scheduled')
        self.assertFalse(surgery.check_consent)
        with self.assertRaises(UserError):
            surgery.action_enter_theatre()
        surgery.line_ids.action_consent()
        consent = surgery.line_ids.consent_id
        consent.signature = PNG
        consent.action_sign()
        self.assertTrue(surgery.check_consent)
        surgery.write({f: True for f in ('check_identity', 'check_site_marked', 'check_anesthesia',
                                         'check_allergy', 'check_fasting')})
        surgery.action_enter_theatre()
        with self.assertRaises(UserError):
            surgery.action_to_recovery()
        surgery.write({f: True for f in ('check_team', 'check_procedure_confirmed', 'check_antibiotic', 'check_imaging',
                                         'check_counts', 'check_specimens', 'check_equipment')})
        surgery.action_to_recovery()
        with self.assertRaises(UserError):
            surgery.action_done()
        surgery.operative_note = '<p>Hamstring autograft, femoral and tibial tunnels, fixation with screws.</p>'
        surgery.action_done()
        self.assertEqual(surgery.state, 'done')
        self.assertIn(surgery.visit_id.state, ('done', 'invoiced'))
        self.assertEqual(case.state, 'healing')
        html = self.env['ir.actions.report']._render_qweb_html('medical_core.report_medical_surgery', surgery.ids)[0]
        self.assertIn(b'Hamstring autograft', html)
