from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.medical_core.tests.common import MedicalCommon


@tagged('post_install', '-at_install', 'medical')
class TestOphthalmology(MedicalCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eye = cls.env.ref('medical_ophthalmology.dept_ophthalmology')
        cls.ophthalmologist = cls.env['hr.employee'].create({
            'name': 'Dr. Reem (eye)', 'is_practitioner': True, 'medical_department_ids': [(6, 0, cls.eye.ids)]})

    def _visit(self, **kw):
        return self._create_visit([{'product_id': self.env.ref('medical_ophthalmology.product_eye_consultation').id}],
                                  doctor_id=self.ophthalmologist.id, room_id=False, **kw)

    def test_eye_exam_measurements(self):
        visit = self._visit()
        self.assertTrue(visit.is_eye)
        visit.write({'iop_od': 26, 'iop_os': 15, 'va_od_cc': '6/12', 'va_os_cc': '6/6', 'cdr_od': 0.7})
        obs = self.env['medical.observation'].search([('visit_id', '=', visit.id)])
        iop_right = obs.filtered(lambda o: o.type_id.code == 'iop' and o.laterality == 'right')
        self.assertEqual(iop_right.value, 26)
        self.assertEqual(iop_right.flag, 'high')
        va_left = obs.filtered(lambda o: o.type_id.code == 'va_logmar' and o.laterality == 'left')
        self.assertEqual(va_left.value, 0.0, "6/6 is logMAR 0")
        va_right = obs.filtered(lambda o: o.type_id.code == 'va_logmar' and o.laterality == 'right')
        self.assertEqual(va_right.value, 0.3)
        self.assertEqual(va_right.flag, 'high')
        visit.iop_od = 0
        self.assertFalse(self.env['medical.observation'].search(
            [('visit_id', '=', visit.id), ('type_id.code', '=', 'iop'), ('laterality', '=', 'right')]))
        with self.assertRaises(ValidationError):
            visit.od_axis = 190

    def test_glasses_prescription(self):
        visit = self._visit()
        visit.write({'od_sph': -1.5, 'od_cyl': -0.75, 'od_axis': 90, 'os_sph': -1.25, 'od_add': 1.5, 'os_add': 1.5})
        visit.action_check_in()
        action = visit.action_eye_prescription()
        rx = self.env['medical.eye.prescription'].browse(action['res_id'])
        self.assertEqual(rx.od_cyl, -0.75)
        self.assertEqual(rx.rx_type, 'progressive')
        self.assertTrue(rx.name.startswith('GRX/'))
        html = self.env['ir.actions.report']._render_qweb_html('medical_ophthalmology.report_eye_prescription', rx.ids)[0]
        self.assertIn(b'-1.50', html)

    def test_eye_side_label_and_cataract_iol(self):
        visit = self._visit()
        line = self.env['medical.treatment.line'].create({
            'visit_id': visit.id, 'product_id': self.env.ref('medical_ophthalmology.product_ivi').id, 'laterality': 'left'})
        self.assertEqual(line.site_label, 'Left eye (OS)')
        self.assertEqual(line.site_key, 'eye:left')
        surgery = self.env['medical.surgery'].create({
            'patient_id': self.patient.id, 'surgeon_id': self.ophthalmologist.id,
            'room_id': self.env.ref('medical_core.room_theatre_1').id, 'scheduled_start': '2026-11-03 08:00:00',
            'laterality': 'right', 'iol_model': 'Acrylic monofocal', 'iol_power': 21.5, 'axial_length': 23.4})
        self.assertTrue(surgery.is_eye)
        self.assertEqual(surgery.iol_power, 21.5)
