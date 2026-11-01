from odoo.addons.medical_core.tests.common import MedicalCommon


def setup_dental_data(cls):
    """Dental fixtures on top of the medical ones."""
    env = cls.env
    cls.dentistry = env.ref('dental_core.dept_dentistry')
    cls.dentist = env['hr.employee'].create({
        'name': 'Dr. Karim (dentist)', 'is_practitioner': True,
        'medical_department_ids': [(6, 0, cls.dentistry.ids)],
        'main_specialty_id': env.ref('dental_core.spec_restorative').id,
    })
    cls.chair = env['medical.room'].create({'name': 'Chair 1', 'room_type': 'chair', 'department_id': cls.dentistry.id})
    cls.tooth_36 = env.ref('dental_core.tooth_36')
    cls.tooth_11 = env.ref('dental_core.tooth_11')
    cls.proc_filling = env['product.product'].create({
        'name': 'Composite filling (test)',
        'type': 'service',
        'is_medical_procedure': True,
        'specialty_id': env.ref('dental_core.spec_restorative').id,
        'site_requirement': 'tooth',
        'dental_requires_surface': True,
        'dental_chart_condition': 'filled',
        'list_price': 1000.0,
    })
    cls.proc_dental_consult = env.ref('dental_core.product_dental_consultation')


class DentalCommon(MedicalCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        setup_dental_data(cls)

    @classmethod
    def _create_dental_visit(cls, lines=None, **kw):
        return cls._create_visit(lines, **dict({'doctor_id': cls.dentist.id, 'room_id': cls.chair.id}, **kw))
