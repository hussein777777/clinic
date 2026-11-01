from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.dental_core.tests.common import DentalCommon


@tagged('post_install', '-at_install', 'medical')
class TestProsthodontics(DentalCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lab = cls.env['res.partner'].create({'name': 'Smile Lab', 'is_company': True, 'is_dental_lab': True})
        cls.lab_service = cls.env.ref('dental_prosthodontics.product_lab_zirconia_crown')
        cls.env['product.supplierinfo'].create({
            'partner_id': cls.lab.id, 'product_tmpl_id': cls.lab_service.product_tmpl_id.id, 'price': 1100.0,
        })
        cls.crown = cls.env.ref('dental_prosthodontics.product_zirconia_crown')

    def test_lab_flow_purchase_and_execution(self):
        plan = self.env['medical.treatment.plan'].create({
            'patient_id': self.patient.id,
            'doctor_id': self.dentist.id,
            'line_ids': [(0, 0, {'product_id': self.crown.id, 'tooth_id': self.tooth_36.id})],
        })
        plan.action_present()
        plan.action_accept()
        line = plan.line_ids
        order = line.lab_order_ids
        self.assertEqual(len(order), 1, "Accepting the plan creates the lab order")
        self.assertEqual(order.lab_partner_id, self.lab)

        visit = self._create_visit()
        line.visit_id = visit
        with self.assertRaises(UserError):
            visit.action_done()

        order.action_send_to_lab()
        po = order.purchase_ids
        self.assertEqual(po.state, 'purchase')
        self.assertEqual(po.order_line.price_unit, 1100.0)
        self.assertEqual(po.order_line.medical_line_id, line)
        self.assertEqual(order.lab_cost, 1100.0)
        self.assertTrue(order.lab_cost_is_estimate)

        order.action_mark_received()
        self.assertEqual(po.order_line.qty_received, 1)
        self.assertEqual(po.invoice_status, 'to invoice')

        # clinic-fault remake adds a second purchase order
        self.env['dental.lab.remake.wizard'].create({
            'lab_order_id': order.id, 'fault': 'clinic', 'reason': 'Wrong impression'}).action_confirm()
        self.assertEqual(order.remake_count, 1)
        self.assertEqual(len(order.purchase_ids), 2)
        self.assertEqual(line.lab_cost, 2200.0)

        order.action_mark_received()
        order.action_deliver()
        visit.action_done()
        self.assertEqual(line.state, 'executed')
        self.assertEqual(self.patient.dental_chart_data['36']['condition'], 'crown')
