{
    'name': 'Medical Center Suite',
    'version': '19.0.1.0.0',
    'category': 'Healthcare',
    'summary': 'Installs every clinic of the medical center: dentistry, orthopedics, neurology, internal medicine, '
               'pediatrics, ophthalmology, dermatology, OB/GYN, with insurance, accounting, stock, dashboard and AI',
    'description': """
Medical Center Suite
====================
One click installs the whole polyclinic. Each clinic is also a separate module, so a center can install only the
clinics it runs.
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': [
        'medical_core', 'medical_insurance', 'medical_account', 'medical_stock', 'medical_dashboard', 'medical_ai',
        'dental_core', 'dental_restorative', 'dental_prosthodontics', 'dental_orthodontics',
        'medical_orthopedics', 'medical_neurology', 'medical_internal', 'medical_pediatrics',
        'medical_ophthalmology', 'medical_dermatology', 'medical_obgyn',
    ],
    'application': True,
    'installable': True,
}
