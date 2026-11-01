{
    'name': 'Medical Center Core',
    'version': '19.0.1.0.0',
    'category': 'Healthcare',
    'summary': 'Polyclinic base: clinics, doctors, rooms, patients, visits, diagnoses, prescriptions, lab & imaging, programs',
    'description': """
Medical Center Core
===================
* Clinics / departments, specialties, rooms, chairs and devices, doctors (hr.employee)
* Patients on res.partner: allergies, alerts, blood group, problem list, medical history
* Visits with vital signs, consultation notes, ICD-10 diagnoses, follow-up booking and referrals
* Treatment plans and treatment lines with body site and side
* Prescriptions with allergy check, lab and imaging orders with results, measurements and trends
* Clinical scores engine (questionnaires with scoring and interpretation)
* Care programs (packages paid once, earned monthly or per session) and signed consent forms
* Theatre: operations with the WHO surgical safety checklist and operative notes
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['mail', 'product', 'hr'],
    'data': [
        'security/medical_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/medical_catalog_data.xml',
        'data/medical_body_site_data.xml',
        'data/medical_diagnosis_data.xml',
        'data/medical_medicine_data.xml',
        'report/medical_reports.xml',
        'wizard/medical_visit_add_lines_views.xml',
        'views/medical_config_views.xml',
        'views/product_views.xml',
        'views/hr_employee_views.xml',
        'views/medical_treatment_line_views.xml',
        'views/medical_treatment_plan_views.xml',
        'views/medical_prescription_views.xml',
        'views/medical_order_views.xml',
        'views/medical_misc_views.xml',
        'views/medical_visit_views.xml',
        'views/res_partner_views.xml',
        'views/medical_menus.xml',
        'views/medical_surgery_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'medical_core/static/src/observation_trend/observation_trend.js',
            'medical_core/static/src/observation_trend/observation_trend.xml',
            'medical_core/static/src/observation_trend/observation_trend.scss',
        ],
        'web.assets_web_dark': [
            'medical_core/static/src/observation_trend/observation_trend.dark.scss',
        ],
    },
    'application': True,
    'installable': True,
}
