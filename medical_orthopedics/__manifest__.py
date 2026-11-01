{
    'name': 'Medical Center - Orthopedics & Physiotherapy',
    'version': '19.0.1.0.0',
    'category': 'Healthcare/Orthopedics',
    'summary': 'Fractures and joint problems from injury to recovery: casts, imaging, surgery, ROM, physiotherapy',
    'description': """
Orthopedics & Physiotherapy
===========================
* Orthopedic cases: injury, site and side, fracture details, treatment, weight bearing, expected healing
* Casts, splints and braces with removal reminders and an overdue list
* Range of motion and grip strength measurements per side, Lysholm and Oswestry scores, trends
* Surgery booking from the case (arthroscopy, ACL, joint replacement, ORIF) with implant traceability
* Physiotherapy programs sold as session packages, session notes (modalities, pain before / after), home exercises
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/orthopedics_data.xml',
        'report/orthopedics_reports.xml',
        'views/orthopedics_views.xml',
    ],
    'installable': True,
}
