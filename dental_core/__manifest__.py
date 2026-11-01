{
    'name': 'Medical Center - Dentistry',
    'version': '19.0.1.0.0',
    'category': 'Healthcare/Dentistry',
    'summary': 'Dental clinic: FDI tooth chart, tooth / surface procedures, dental specialties',
    'description': """
Dentistry
=========
* Dentistry clinic with its specialties (restorative, endo, surgery, prostho, ortho, perio, implants)
* FDI tooth chart (permanent + primary teeth) with per-tooth conditions, updated when procedures are executed
* Tooth and surfaces on treatment lines; tooth-specific procedures cannot be closed without them
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/dental_tooth_data.xml',
        'data/dental_data.xml',
        'views/dental_tooth_views.xml',
        'views/dental_chart_line_views.xml',
        'views/dental_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dental_core/static/src/tooth_chart/*',
        ],
    },
    'installable': True,
}
