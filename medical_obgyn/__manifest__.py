{
    'name': 'Medical Center - Obstetrics & Gynecology',
    'version': '19.0.1.0.0',
    'category': 'Healthcare/Obstetrics',
    'summary': 'Pregnancy follow-up: EDD and gestational age, WHO antenatal contacts, ultrasounds, alerts, delivery, maternity packages',
    'description': """
Obstetrics & Gynecology
=======================
* Pregnancy record: LMP and ultrasound dating, EDD, gestational age, G/P/A/L, risk factors and high-risk flag
* The 8 WHO antenatal contacts planned automatically, booked in one click, matched to visits, overdue list
* Antenatal examination on the visit (fundal height, fetal heart, presentation, movements, urine dipstick)
  with alerts for high blood pressure, proteinuria, abnormal fetal heart rate and reduced movements
* Obstetric ultrasounds (biometry, EFW, AFI, placenta) that can re-date the pregnancy
* Delivery outcome and registration of the baby as a patient; maternity packages billed as care programs
* Gynecology services (Pap smear, colposcopy, IUD, D and C, caesarean section) with consent; EPDS and Bishop scores
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/obgyn_data.xml',
        'views/obgyn_views.xml',
    ],
    'installable': True,
}
