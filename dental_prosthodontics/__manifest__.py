{
    'name': 'Medical Center - Dentistry Prosthodontics & Lab',
    'version': '19.0.1.0.0',
    'category': 'Healthcare/Dentistry',
    'summary': 'Crowns, bridges, dentures: lab orders, stages, remakes, purchase orders and lab cost per case',
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['dental_core', 'purchase'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/product_data.xml',
        'views/dental_lab_order_views.xml',
        'views/dental_views.xml',
    ],
    'installable': True,
}
