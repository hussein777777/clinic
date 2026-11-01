{
    'name': 'Medical Center - Management Dashboard',
    'version': '19.0.1.0.0',
    'category': 'Healthcare',
    'summary': 'Production, collections, room occupancy, insurance rejections and revenue leakage at a glance',
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_account'],
    'data': [
        'data/ir_config_parameter_data.xml',
        'views/medical_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'medical_dashboard/static/src/dashboard/dashboard.js',
            'medical_dashboard/static/src/dashboard/dashboard.xml',
            'medical_dashboard/static/src/dashboard/dashboard.scss',
        ],
        'web.assets_tests': [
            'medical_dashboard/static/tests/tours/*.js',
        ],
        'web.assets_web_dark': [
            'medical_dashboard/static/src/dashboard/dashboard.dark.scss',
        ],
    },
    'installable': True,
}
