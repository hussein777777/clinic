from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import DentalCommon


@tagged('post_install', '-at_install', 'medical')
class TestDentalCore(DentalCommon):

    def test_teeth_and_department(self):
        self.assertEqual(self.env['dental.tooth'].search_count([]), 52)
        visit = self._create_dental_visit()
        self.assertEqual(visit.department_id, self.dentistry)
        self.assertTrue(self.proc_filling.is_dental)
        self.assertIn(self.proc_filling.product_tmpl_id,
                      self.env['product.template'].search([('is_dental', '=', True)]))

    def test_plan_to_visit_execution_updates_chart(self):
        plan = self.env['medical.treatment.plan'].create({
            'patient_id': self.patient.id,
            'doctor_id': self.dentist.id,
            'line_ids': [(0, 0, {'product_id': self.proc_filling.id, 'tooth_id': self.tooth_36.id, 'surfaces': 'mod'}),
                         (0, 0, {'product_id': self.proc_dental_consult.id})],
        })
        self.assertEqual(plan.department_id, self.dentistry)
        self.assertEqual(plan.line_ids[0].surfaces, 'MOD')
        self.assertEqual(plan.line_ids[0].site_label, 'tooth 36 MOD')
        self.assertEqual(plan.line_ids[0].site_key, 'tooth:36')
        plan.action_present()
        plan.action_accept()
        visit = self._create_dental_visit()
        self.env['medical.visit.add.lines'].create({'visit_id': visit.id, 'line_ids': [(6, 0, plan.line_ids.ids)]}).action_confirm()
        visit.action_check_in()
        visit.action_done()
        self.assertEqual(plan.state, 'done')
        chart = self.patient.chart_line_ids
        self.assertEqual(len(chart), 1)
        self.assertEqual(chart.condition, 'filled')
        self.assertEqual(self.patient.dental_chart_data['36']['condition'], 'filled')

    def test_tooth_and_surface_required(self):
        visit = self._create_dental_visit([{'product_id': self.proc_filling.id}])
        with self.assertRaisesRegex(UserError, 'requires a tooth'):
            visit.action_done()
        visit.line_ids.tooth_id = self.tooth_36
        with self.assertRaisesRegex(UserError, 'surfaces'):
            visit.action_done()
        visit.line_ids.surfaces = 'o'
        visit.action_done()
        with self.assertRaises(UserError):
            visit.line_ids.tooth_id = self.tooth_11
