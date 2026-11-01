{
    'name': 'Medical Center - Pediatrics',
    'version': '19.0.1.0.0',
    'category': 'Healthcare/Pediatrics',
    'summary': 'WHO growth charts and z-scores, vaccination schedule and reminders, milestones, weight-based doses',
    'description': """
Pediatrics
==========
* WHO Child Growth Standards (birth to 5 years): weight, length/height, head circumference and BMI for age,
  z-scores, percentiles and WHO classification (underweight, stunted, wasted, overweight)
* Interactive growth chart on the child's file with percentile curves and a table view
* Immunisation schedule (editable), vaccination plan generated from the date of birth, due list and reminders,
  lot and site recorded, billing of the vaccine on the visit
* Developmental milestones checklist by age
* Weight-based doses on prescriptions (mg/kg, maximum dose, millilitres from the syrup concentration)
* APGAR score, pediatric ICD-10 codes and services
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/medical.growth.reference.csv',
        'data/pediatrics_data.xml',
        'data/ir_cron_data.xml',
        'views/pediatrics_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'medical_pediatrics/static/src/growth_chart/growth_chart.js',
            'medical_pediatrics/static/src/growth_chart/growth_chart.xml',
            'medical_pediatrics/static/src/growth_chart/growth_chart.scss',
        ],
        'web.assets_web_dark': [
            'medical_pediatrics/static/src/growth_chart/growth_chart.dark.scss',
        ],
    },
    'installable': True,
}
