from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.dental_core.tests.common import DentalCommon


@tagged('post_install', '-at_install', 'medical')
class TestOrthodontics(DentalCommon):

    def _case(self, **kw):
        vals = {
            'care_type': 'orthodontics',
            'patient_id': self.patient.id,
            'doctor_id': self.dentist.id,
            'product_id': self.env.ref('dental_orthodontics.product_ortho_metal').id,
            'session_product_id': self.env.ref('dental_orthodontics.product_ortho_adjustment').id,
            'down_payment': 6000,
            'installment_count': 24,
            'est_months': 24,
        }
        vals.update(kw)
        return self.env['medical.care.contract'].create(vals)

    def test_case_and_adjustment(self):
        case = self._case()
        self.assertEqual(case.package_price, 30000)
        self.assertEqual(case.installment_amount, 1000)
        self.assertEqual(case.department_id, self.dentistry)
        case.action_start()
        action = case.action_new_session_visit()
        visit = self.env['medical.visit'].browse(action['res_id'])
        self.assertEqual(visit.care_contract_id, case)
        self.assertEqual(visit.line_ids.price_unit, 0.0)
        visit.room_id = self.chair
        visit.action_done()
        self.assertEqual(case.visit_count, 1)

    def test_down_payment_constraint(self):
        with self.assertRaises(ValidationError):
            self._case(down_payment=50000)
