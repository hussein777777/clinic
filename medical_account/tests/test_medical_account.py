from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.medical_core.tests.common import MedicalVisitHelper, setup_medical_data
from odoo.addons.medical_insurance.tests.common import setup_insurance_data


@tagged('post_install', '-at_install', 'medical')
class TestMedicalAccount(MedicalVisitHelper, AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'group_ids': [
            (4, cls.env.ref('medical_core.group_medical_manager').id),
            (4, cls.env.ref('hr.group_hr_user').id),
        ]})
        cls.company = cls.env.company
        cls.company._medical_setup_accounting()
        setup_medical_data(cls)
        setup_insurance_data(cls)
        cls.cash_journal = cls.company_data['default_journal_cash']
        cls.bank_journal = cls.company_data['default_journal_bank']
        cls.cash_patient = cls.env['res.partner'].create({'name': 'Cash Patient', 'is_patient': True})
        # medical services are VAT exempt in most clinics: keep the test procedures tax free
        (cls.proc_main | cls.proc_consult | cls.proc_major).taxes_id = False

    def _pay(self, move, amount=None, journal=None):
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=move.ids).create({
                'journal_id': (journal or self.bank_journal).id,
                **({'amount': amount} if amount is not None else {}),
            })
        return wizard._create_payments()

    # ------------------------------------------------------------------ setup
    def test_setup_accounting(self):
        c = self.company
        self.assertTrue(c.medical_patient_journal_id and c.medical_insurance_journal_id and c.medical_deferred_journal_id)
        self.assertEqual(c.medical_advance_account_id.account_type, 'liability_current')
        self.assertEqual(self.insurer.property_account_receivable_id, c.medical_insurance_receivable_account_id)
        self.assertTrue(self.doctor._medical_analytic_account())

    # -------------------------------------------------------- patient + insurer
    def test_visit_insurer_claim_full_cycle(self):
        visit = self._create_visit([{'product_id': self.proc_main.id, 'laterality': 'right'}])
        visit.action_done()
        self.assertEqual(visit.state, 'invoiced')
        inv = visit.patient_move_id
        self.assertEqual(inv.state, 'posted')
        self.assertEqual(inv.amount_untaxed, 200.0)
        self.assertEqual(inv.journal_id, self.company.medical_patient_journal_id)
        line = inv.invoice_line_ids
        analytic = self.doctor._medical_analytic_account()
        self.assertEqual(line.analytic_distribution, {str(analytic.id): 100.0})
        self.assertEqual(line.medical_doctor_id, self.doctor)

        claim = self.env['medical.claim']._create_batches(False, line.medical_line_id.date_executed)
        claim.action_submit()
        self.assertEqual(claim.move_id.amount_total, 800.0)
        self.assertEqual(claim.move_id.partner_id, self.insurer)
        receivable = claim.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertEqual(receivable.account_id, self.company.medical_insurance_receivable_account_id)

        claim.line_ids.write({'approved': 500.0, 'reject_reason': 'Above tariff'})
        claim.action_settle()
        self.assertEqual(claim.move_id.amount_residual, 500.0)
        refund = claim.refund_move_ids
        self.assertEqual(refund.amount_total, 300.0)

        self._pay(claim.move_id)
        self.assertEqual(claim.state, 'paid')

        claim.line_ids.action_rebill_patient()
        cl = claim.line_ids
        self.assertEqual(cl.state, 'rebilled')
        self.assertEqual(cl.rebill_move_id.amount_total, 300.0)
        self.assertEqual(cl.rebill_move_id.partner_id, self.patient)

        check = self.env['medical.reconciliation.check'].create({})
        self.assertAlmostEqual(check.insurance_diff, 0.0)

    def test_write_off_to_loss(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        visit.action_done()
        claim = self.env['medical.claim']._create_batches(False, visit.line_ids.date_executed)
        claim.action_submit()
        claim.line_ids.write({'approved': 0.0, 'reject_reason': 'Not covered'})
        claim.action_settle()
        self.assertEqual(claim.line_ids.state, 'rejected')
        claim.line_ids.action_write_off()
        move = claim.line_ids.writeoff_move_id
        loss = move.line_ids.filtered(lambda l: l.account_id == self.company.medical_insurance_loss_account_id)
        self.assertEqual(loss.debit, 300.0)

    # ---------------------------------------------------------------- advances
    def test_advance_deducted_on_invoice(self):
        plan = self.env['medical.treatment.plan'].create({
            'patient_id': self.cash_patient.id,
            'doctor_id': self.doctor.id,
            'line_ids': [(0, 0, {'product_id': self.proc_main.id, 'laterality': 'right'}),
                         (0, 0, {'product_id': self.proc_main.id, 'laterality': 'left'})],
        })
        plan.action_present()
        plan.action_accept()
        advance = plan._create_advance_invoice(1500.0)
        self._pay(advance, journal=self.cash_journal)
        self.assertEqual(plan.advance_balance, 1500.0)

        visit = self._create_visit(patient_id=self.cash_patient.id, plan_id=plan.id)
        plan.line_ids[0].visit_id = visit
        visit.action_done()
        inv = visit.patient_move_id
        self.assertEqual(inv.amount_total, 0.0, "1000 of procedure fully covered by the 1500 advance")
        self.assertEqual(plan.advance_balance, 500.0)

        visit2 = self._create_visit(patient_id=self.cash_patient.id, plan_id=plan.id)
        plan.line_ids[1].visit_id = visit2
        visit2.action_done()
        self.assertEqual(visit2.patient_move_id.amount_total, 500.0)
        self.assertEqual(plan.advance_balance, 0.0)
        self.assertEqual(plan.state, 'done')

        check = self.env['medical.reconciliation.check'].create({})
        self.assertAlmostEqual(check.advance_diff, 0.0)

    def test_reverse_visit_invoice(self):
        visit = self._create_visit([{'product_id': self.proc_consult.id}], patient_id=self.cash_patient.id)
        visit.action_done()
        inv = visit.patient_move_id
        visit.action_reverse_invoice()
        self.assertEqual(inv.payment_state, 'reversed')
        self.assertEqual(visit.state, 'done')
        visit.action_reinvoice()
        self.assertEqual(visit.state, 'invoiced')
        self.assertEqual(visit.patient_move_id.amount_total, 300.0)

    # ------------------------------------------------------------ care programs
    def _package(self, name='Program package (test)', price=30000.0):
        return self.env['product.product'].create({
            'name': name, 'type': 'service', 'is_medical_procedure': True, 'procedure_type': 'package',
            'specialty_id': self.spec_minor.id, 'list_price': price, 'taxes_id': False})

    def _ortho_case(self, **kw):
        vals = {
            'patient_id': self.cash_patient.id,
            'doctor_id': self.doctor.id,
            'product_id': self._package().id,
            'package_price': 30000.0,
            'down_payment': 6000.0,
            'installment_count': 24,
            'est_months': 24,
            'start_date': date(2026, 1, 10),
        }
        vals.update(kw)
        return self.env['medical.care.contract'].create(vals)

    def test_ortho_contract_and_recognition(self):
        case = self._ortho_case()
        case.action_start()
        inv = case.contract_move_id
        self.assertEqual(inv.amount_total, 30000.0)
        receivables = inv.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertEqual(len(receivables), 25)
        self.assertEqual(sorted(receivables.mapped('balance'))[-1], 6000.0)
        self.assertEqual(case.deferred_balance, 30000.0)

        case._recognize_until(date(2026, 3, 31))
        self.assertEqual(case.recognized_months, 3)
        self.assertEqual(case.recognized_amount, 3750.0)
        self.assertEqual(case.deferred_balance, 26250.0)
        case._recognize_until(date(2026, 3, 31))  # idempotent
        self.assertEqual(case.recognized_months, 3)
        self.assertTrue(case.is_late, "installments due since January are unpaid")

        case._recognize_until(date(2028, 12, 31))
        self.assertEqual(case.recognized_months, 24)
        self.assertEqual(case.deferred_balance, 0.0)
        self.assertEqual(case.recognized_amount, 30000.0)

        check = self.env['medical.reconciliation.check'].create({'date': date(2030, 1, 1)})
        self.assertAlmostEqual(check.deferred_diff, 0.0)

    def test_ortho_termination(self):
        today = fields.Date.context_today(self.env.user)
        case = self._ortho_case(start_date=today - relativedelta(months=2), est_months=10, installment_count=10,
                                package_price=10000.0, down_payment=1000.0)
        case.action_start()
        self._pay(case.contract_move_id, amount=3000.0)
        case.action_terminate()
        self.assertEqual(case.state, 'terminated')
        self.assertEqual(case.recognized_months, 3)
        self.assertEqual(case.deferred_balance, 0.0)
        self.assertEqual(case.contract_move_id.payment_state, 'paid',
                         "the credit note closes the unpaid installments")
        # 3000 paid, 3000 earned: nothing owed either way
        balance = sum(self.env['account.move.line'].search([
            ('partner_id', '=', self.cash_patient.id), ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable')]).mapped('balance'))
        self.assertAlmostEqual(balance, 0.0)

    # ------------------------------------------------------------- commission
    def test_commission_on_collected(self):
        self.env['medical.commission.rule'].create({'doctor_id': self.doctor.id, 'rate': 30.0})
        visit = self._create_visit([{'product_id': self.proc_consult.id}], patient_id=self.cash_patient.id)
        visit.action_done()
        payment = self._pay(visit.patient_move_id, amount=200.0)
        self.assertIn(visit.patient_move_id.payment_state, ('partial', 'in_payment', 'paid'))
        today = payment.date
        lines = self.env['medical.commission.line']._compute_commissions(today.replace(day=1), today)
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines.base_amount, 200.0)
        self.assertAlmostEqual(lines.amount, 60.0)
        lines.action_approve()
        bills = lines.action_create_bills()
        self.assertEqual(bills.amount_total, 60.0)
        self.assertEqual(bills.partner_id, self.doctor.work_contact_id)

    # --------------------------------------------------------------- cash
    def test_cash_closing(self):
        self.cash_journal.loss_account_id = self.company_data['default_account_expense']
        self.cash_journal.profit_account_id = self.company_data['default_account_revenue']
        visit = self._create_visit([{'product_id': self.proc_consult.id}], patient_id=self.cash_patient.id)
        visit.action_done()
        payment = self._pay(visit.patient_move_id, journal=self.cash_journal)
        closing = self.env['medical.cash.closing'].create({
            'journal_id': self.cash_journal.id, 'date': payment.date, 'amount_counted': 290.0})
        self.assertEqual(closing.amount_expected, 300.0)
        self.assertEqual(closing.difference, -10.0)
        closing.action_close()
        self.assertEqual(closing.state, 'closed')
        self.assertEqual(closing.difference_move_id.state, 'posted')

    # ----------------------------------------------------------- accruals
    def test_insurance_accrual(self):
        self.company.medical_insurance_accrual = True
        visit = self._create_visit([{'product_id': self.proc_consult.id}])
        visit.action_done()
        period_end = fields.Date.end_of(visit.line_ids.date_executed, 'month')
        move = self.env['medical.claim']._post_insurance_accrual(self.company, period_end)
        self.assertEqual(move.state, 'posted')
        unbilled = move.line_ids.filtered(lambda l: l.account_id == self.company.medical_unbilled_insurance_account_id)
        self.assertEqual(unbilled.debit, 300.0)
        self.assertTrue(move.reversal_move_ids)
        again = self.env['medical.claim']._post_insurance_accrual(self.company, period_end)
        self.assertFalse(again, "one accrual per period")

    def test_session_program_revenue(self):
        session = self.env['product.product'].create({
            'name': 'Physio session (test)', 'type': 'service', 'is_medical_procedure': True,
            'procedure_type': 'session', 'specialty_id': self.spec_minor.id, 'list_price': 400.0, 'taxes_id': False})
        contract = self.env['medical.care.contract'].create({
            'patient_id': self.cash_patient.id, 'doctor_id': self.doctor.id,
            'product_id': self._package('10 sessions (test)', 3000.0).id,
            'billing_mode': 'sessions', 'session_count': 3, 'session_product_id': session.id,
        })
        contract.action_start()
        self.assertEqual(contract.contract_move_id.amount_total, 3000.0)
        self.assertEqual(contract.deferred_balance, 3000.0)
        for _i in range(2):
            visit = self.env['medical.visit'].browse(contract.action_new_session_visit()['res_id'])
            visit.action_done()
            self.assertFalse(visit.patient_move_id, "sessions are paid through the program")
        self.assertEqual(contract.recognized_amount, 2000.0)
        self.assertEqual(contract.deferred_balance, 1000.0)
        contract.action_terminate()
        self.assertEqual(contract.deferred_balance, 0.0, "the unused session is credited back")
        refund = contract.move_ids.filtered(lambda m: m.medical_move_kind == 'program_termination')
        self.assertEqual(refund.amount_total, 1000.0)
