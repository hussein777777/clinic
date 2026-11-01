from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.medical_core.tests.common import MedicalCommon


@tagged('post_install', '-at_install', 'medical')
class TestMedicalStock(MedicalCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.resin = cls.env['product.product'].create({'name': 'Steroid vial (ml)', 'type': 'consu', 'is_storable': True})
        cls.implant = cls.env['product.product'].create({
            'name': 'Knee implant (test)', 'type': 'consu', 'is_storable': True, 'tracking': 'serial'})
        cls.proc_main.product_tmpl_id.medical_consumable_ids = [(0, 0, {'product_id': cls.resin.id, 'quantity': 0.5})]
        cls.proc_implant = cls.env['product.product'].create({
            'name': 'Knee replacement (test)', 'type': 'service', 'is_medical_procedure': True,
            'specialty_id': cls.spec_major.id, 'site_requirement': 'side', 'procedure_type': 'surgery',
            'list_price': 15000.0,
            'medical_consumable_ids': [(0, 0, {'product_id': cls.implant.id, 'quantity': 1})],
        })
        src, _dest = cls.env.company._medical_stock_locations()
        cls.env['stock.quant']._update_available_quantity(cls.resin, src, 10.0)
        cls.serial = cls.env['stock.lot'].create({'name': 'IMP-0001', 'product_id': cls.implant.id})
        cls.env['stock.quant']._update_available_quantity(cls.implant, src, 1.0, lot_id=cls.serial)

    def test_material_consumed_on_execution(self):
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        visit.action_done()
        self.assertEqual(self.resin.qty_available, 9.5)
        self.assertEqual(visit.line_ids.stock_move_ids.state, 'done')

    def test_implant_requires_serial(self):
        visit = self._create_visit([{'product_id': self.proc_implant.id, 'laterality': 'right'}])
        with self.assertRaises(UserError):
            visit.action_done()
        visit.line_ids.lot_id = self.serial
        visit.action_done()
        self.assertEqual(self.implant.qty_available, 0.0)
        self.assertEqual(visit.line_ids.stock_move_ids.move_line_ids.lot_id, self.serial)
        traced = self.env['medical.treatment.line'].search([('lot_id', '=', self.serial.id)])
        self.assertEqual(traced.patient_id, self.patient)
