{
    'name': 'Medical Center - Ophthalmology',
    'version': '19.0.1.0.0',
    'category': 'Healthcare/Ophthalmology',
    'summary': 'Eye exam per eye (VA, refraction, IOP, fundus), glasses prescriptions, OCT / fields, lasers, cataract surgery',
    'description': """
Ophthalmology
=============
* Eye examination on the visit, right and left side by side: visual acuity, refraction, IOP, anterior segment, fundus, cup/disc
* IOP, visual acuity (logMAR) and cup/disc tracked per eye as measurements, with trends and out-of-range flags
* Glasses prescription prepared from the refraction and printed
* OCT, visual fields, fundus photography and biometry as tests; YAG, SLT, PRP lasers and anti-VEGF injections with consent
* Cataract surgery in theatre with biometry and IOL details; eye procedures labelled OD / OS / OU
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/ophthalmology_data.xml',
        'report/ophthalmology_reports.xml',
        'views/ophthalmology_views.xml',
    ],
    'installable': True,
}
