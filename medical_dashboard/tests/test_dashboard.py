from datetime import date, datetime

from odoo.tests import HttpCase, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.medical_core.tests.common import MedicalVisitHelper, setup_medical_data
from odoo.addons.medical_insurance.tests.common import setup_insurance_data


@tagged('post_install', '-at_install', 'medical')
class TestMedicalDashboard(MedicalVisitHelper, AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'group_ids': [(4, cls.env.ref('medical_core.group_medical_manager').id),
                                          (4, cls.env.ref('hr.group_hr_user').id)]})
        cls.env.company._medical_setup_accounting()
        setup_medical_data(cls)
        setup_insurance_data(cls)
        (cls.proc_main | cls.proc_consult).taxes_id = False

    def test_period_bounds(self):
        Dash = self.env['medical.dashboard']
        today = date(2026, 10, 10)
        self.assertEqual(Dash._period_bounds('this_month', today),
                         (date(2026, 10, 1), date(2026, 10, 10), date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(Dash._period_bounds('last_month', today)[:2], (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(Dash._period_bounds('this_year', today)[2:], (date(2025, 1, 1), date(2025, 10, 10)))

    def test_dashboard_numbers(self):
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}],
                                   start=datetime.now().replace(hour=8, minute=0, second=0, microsecond=0))
        visit.action_done()
        no_show = self._create_visit([], start=datetime.now().replace(hour=9, minute=0, second=0, microsecond=0))
        no_show.action_no_show()
        plan = self.env['medical.treatment.plan'].create({
            'patient_id': self.patient.id, 'doctor_id': self.doctor.id,
            'line_ids': [(0, 0, {'product_id': self.proc_consult.id})]})
        plan.action_present()
        plan.action_accept()

        data = self.env['medical.dashboard'].get_dashboard_data('this_month')
        kpis = data['kpis']
        self.assertEqual(kpis['production'], 1000.0)
        self.assertEqual(kpis['insurer_share'], 800.0)
        self.assertEqual(kpis['visits'], 1)
        self.assertEqual(kpis['no_show_rate'], 50.0)
        self.assertEqual(data['by_doctor'][0]['amount'], 1000.0)
        self.assertEqual(data['trend'][-1]['patient'], 200.0)
        self.assertEqual(data['trend'][-1]['insurer'], 800.0)
        leaks = {item['key']: item for item in data['leakage']}
        self.assertEqual(leaks['unscheduled']['amount'], 300.0)
        self.assertEqual(leaks['to_claim']['amount'], 800.0)
        self.assertEqual(len(data['occupancy']['cells']), 7 * len(data['occupancy']['hours']))
        # doctor filter
        other = self.env['hr.employee'].create({'name': 'Dr. Other', 'is_practitioner': True})
        self.assertEqual(self.env['medical.dashboard'].get_dashboard_data('this_month', other.id)['kpis']['production'], 0.0)
        # clinic breakdown, then clinic filter
        self.assertEqual(data['breakdown'], 'department')
        self.assertEqual(data['by_specialty'][0]['name'], self.department.name)
        clinic = self.env['medical.dashboard'].get_dashboard_data('this_month', False, self.department.id)
        self.assertEqual(clinic['kpis']['production'], 1000.0)
        self.assertEqual(clinic['breakdown'], 'specialty')
        self.assertEqual(clinic['by_specialty'][0]['name'], self.spec_minor.name)
        empty = self.env['medical.department'].create({'name': 'Empty clinic', 'code': 'EMP'})
        none = self.env['medical.dashboard'].get_dashboard_data('this_month', False, empty.id)
        self.assertEqual(none['kpis']['production'], 0.0)
        self.assertEqual(none['kpis']['visits'], 0)


@tagged('post_install', '-at_install', 'medical')
class TestMedicalDashboardUI(HttpCase):

    def test_dashboard_loads(self):
        action = self.env.ref('medical_dashboard.action_medical_dashboard')
        self.start_tour(f"/odoo/action-{action.id}", 'medical_dashboard_tour', login='admin')
