from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.dental_core.tests.common import DentalCommon


@tagged('post_install', '-at_install', 'medical')
class TestRestorative(DentalCommon):

    def test_surface_pricing(self):
        composite = self.env.ref('dental_restorative.product_composite_filling')
        visit = self._create_dental_visit([{'product_id': composite.id, 'tooth_id': self.tooth_36.id, 'surfaces': 'MOD'}])
        line = visit.line_ids
        self.assertEqual(line.price_unit, 800 + 2 * 200)
        self.assertEqual(line.material, 'composite')

    def test_extraction_requires_consent(self):
        extraction = self.env.ref('dental_restorative.product_simple_extraction')
        visit = self._create_dental_visit([{'product_id': extraction.id, 'tooth_id': self.tooth_36.id}])
        with self.assertRaises(UserError):
            visit.action_done()
        visit.line_ids.action_consent()
        consent = visit.line_ids.consent_id
        self.assertIn('dry socket', consent.consent_text)
        consent.signature = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
        consent.action_sign()
        visit.action_done()
        self.assertEqual(self.patient.dental_chart_data['36']['condition'], 'extracted')

    def test_reports_render(self):
        extraction = self.env.ref('dental_restorative.product_simple_extraction')
        visit = self._create_dental_visit([{'product_id': extraction.id, 'tooth_id': self.tooth_36.id}],
                                          post_op_instructions='<p>No rinsing today.</p>')
        self.assertTrue(visit.is_dental)
        html = self.env['ir.actions.report']._render_qweb_html('dental_restorative.report_dental_post_op', visit.ids)[0]
        self.assertIn(b'No rinsing today', html)
