{
    'name': 'Medical Center - Internal Medicine',
    'version': '19.0.1.0.0',
    'category': 'Healthcare/Internal Medicine',
    'summary': 'Chronic disease programs (diabetes, hypertension...): targets, control status, due tests, recall',
    'description': """
Internal Medicine
=================
* Chronic programs (diabetes, hypertension, dyslipidaemia, hypothyroidism, CKD) with targets, periodic tests and review interval
* Patients are enrolled automatically when a chronic diagnosis is confirmed
* Control status recomputed from the latest results (HbA1c, BP, LDL...) and shown on the visit and the patient file
* Due tests ordered in one click; daily recall reminders for reviews that are due
* PHQ-9 and GAD-7 (with safety alert), CHA2DS2-VASc; ECG, echo, Holter, spirometry and infusion services
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/internal_data.xml',
        'data/ir_cron_data.xml',
        'views/internal_views.xml',
    ],
    'installable': True,
}
