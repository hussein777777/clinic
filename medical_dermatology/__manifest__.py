{
    'name': 'Medical Center - Dermatology',
    'version': '19.0.1.0.0',
    'category': 'Healthcare/Dermatology',
    'summary': 'Skin lesions with ABCDE screening and photo follow-up, laser and aesthetic programs, before / after photos',
    'description': """
Dermatology
===========
* Skin lesions per patient: site, type, size, ABCDE screening with a suspicious flag, regular check reminders
* Dated clinical photos per lesion, per visit and per program (before / during / after), shown as galleries
* Laser and aesthetic programs sold as session packages, laser settings per session (fluence, spot, pulse),
  copied from the previous session; Fitzpatrick skin type on the patient
* Biopsy, excision, cryotherapy, peels, toxin and fillers with consent forms; itch and acne severity scores
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/dermatology_data.xml',
        'views/dermatology_views.xml',
    ],
    'installable': True,
}
