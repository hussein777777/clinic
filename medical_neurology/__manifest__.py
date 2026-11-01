{
    'name': 'Medical Center - Neurology',
    'version': '19.0.1.0.0',
    'category': 'Healthcare/Neurology',
    'summary': 'Neurological examination, stroke and cognitive scores, EEG / EMG / NCS studies, seizure and headache diary',
    'description': """
Neurology
=========
* Structured neurological examination on the visit (power, reflexes, plantar, sensation, gait) with automatic focal-deficit flag
* Scores: Glasgow Coma Scale, NIH Stroke Scale, modified Rankin, MoCA, MIDAS, stored as trended measurements
* EEG, EMG, nerve conduction and evoked potentials as orders with reports and device rooms
* Seizure and headache diary per patient: seizure-free days, seizures in 90 days, headache days, monthly chart
* Lumbar puncture, botulinum toxin and nerve blocks with consent forms; neurology ICD-10 codes
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/neurology_data.xml',
        'views/neurology_views.xml',
    ],
    'installable': True,
}
