from odoo.tests.common import TransactionCase


def setup_medical_data(cls):
    """Shared fixtures; also used by test classes that extend other test bases (accounting)."""
    env = cls.env
    cls.department = env['medical.department'].create({'name': 'Test Clinic', 'code': 'TST'})
    Specialty = env['medical.specialty']
    cls.spec_minor = Specialty.create({'name': 'Minor procedures', 'code': 'T-MINOR', 'department_id': cls.department.id})
    cls.spec_major = Specialty.create({'name': 'Major procedures', 'code': 'T-MAJOR', 'department_id': cls.department.id})
    cls.spec_excluded = Specialty.create({'name': 'Cosmetic', 'code': 'T-COSM', 'department_id': cls.department.id})
    cls.doctor = env['hr.employee'].create({
        'name': 'Dr. Mona', 'is_practitioner': True, 'medical_department_ids': [(6, 0, cls.department.ids)],
        'main_specialty_id': cls.spec_minor.id,
    })
    cls.room = env['medical.room'].create({'name': 'Room 1', 'department_id': cls.department.id})
    cls.patient = env['res.partner'].create({'name': 'Ahmed Ali', 'is_patient': True, 'gender': 'male',
                                             'birthdate': '1985-03-01'})
    Product = env['product.product']
    cls.proc_consult = env.ref('medical_core.product_consultation')
    cls.proc_main = Product.create({
        'name': 'Joint injection (test)', 'type': 'service', 'is_medical_procedure': True,
        'specialty_id': cls.spec_minor.id, 'site_requirement': 'side', 'list_price': 1000.0,
        'insurance_code': 'T-100',
    })
    cls.proc_major = Product.create({
        'name': 'Arthroscopy (test)', 'type': 'service', 'is_medical_procedure': True,
        'specialty_id': cls.spec_major.id, 'procedure_type': 'surgery', 'list_price': 4000.0,
        'insurance_code': 'T-400',
    })
    cls.proc_excluded = Product.create({
        'name': 'Cosmetic peel (test)', 'type': 'service', 'is_medical_procedure': True,
        'specialty_id': cls.spec_excluded.id, 'list_price': 2000.0, 'insurance_code': 'T-200',
    })


class MedicalVisitHelper:

    @classmethod
    def _create_visit(cls, lines=None, **kw):
        vals = {
            'patient_id': cls.patient.id,
            'doctor_id': cls.doctor.id,
            'room_id': cls.room.id,
        }
        vals.update(kw)
        visit = cls.env['medical.visit'].create(vals)
        for line in lines or []:
            cls.env['medical.treatment.line'].create(dict(line, visit_id=visit.id))
        return visit


class MedicalCommon(MedicalVisitHelper, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        setup_medical_data(cls)
