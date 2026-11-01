{
    'name': 'Medical Center - Consumables & Implants Stock',
    'version': '19.0.1.0.0',
    'category': 'Healthcare',
    'summary': 'Consume materials per procedure, implants by serial number with patient traceability',
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_core', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/medical_stock_views.xml',
    ],
    'installable': True,
}
