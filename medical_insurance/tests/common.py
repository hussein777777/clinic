from datetime import date

from odoo.addons.medical_core.tests.common import MedicalCommon


def setup_insurance_data(cls):
    cls.insurer = cls.env['res.partner'].create({'name': 'MedNet Test', 'is_company': True, 'is_insurer': True})
    cls.ins_plan = cls.env['medical.insurance.plan'].create({
        'name': 'Gold',
        'insurer_id': cls.insurer.id,
        'annual_limit': 5000.0,
        'rule_ids': [
            (0, 0, {'specialty_id': cls.spec_minor.id, 'coverage_pct': 80.0, 'sequence': 10}),
            (0, 0, {'specialty_id': cls.spec_major.id, 'coverage_pct': 50.0, 'requires_preauth': True, 'sequence': 20}),
            (0, 0, {'specialty_id': cls.spec_excluded.id, 'excluded': True, 'sequence': 30}),
            (0, 0, {'coverage_pct': 100.0, 'sequence': 99}),  # catch-all
        ],
    })
    today = date.today()
    cls.policy = cls.env['medical.patient.policy'].create({
        'patient_id': cls.patient.id,
        'plan_id': cls.ins_plan.id,
        'member_no': 'M-0001',
        'date_from': today.replace(month=1, day=1),
        'date_to': today.replace(month=12, day=31),
    })


class MedicalInsuranceCommon(MedicalCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        setup_insurance_data(cls)
