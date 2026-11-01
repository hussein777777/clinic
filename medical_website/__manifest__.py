{
    'name': 'Medical Center - Website & Online Booking',
    'version': '19.0.1.0.0',
    'category': 'Healthcare',
    'summary': 'Bilingual clinic website (Arabic / English): clinics, doctors, services and online appointment booking',
    'description': """
Medical Center Website & Online Booking
=======================================
* Clinic pages in Arabic and English: introduction, clinics, doctors and services, opening hours, map and WhatsApp
* Online booking: clinic, doctor (or the first one free), day and time from the doctor's working hours and
  the real schedule, then the patient's details
* Bookings land in the reception schedule as website bookings; reception confirms them, or they confirm themselves
* The patient gets a booking page to check or cancel the visit, and a confirmation e-mail when an address is given
* Spam guard (hidden field, limit per phone and per address) and no patient data shown to the public

Works for a dental clinic alone or a full medical center.
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['website', 'medical_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/medical_website_data.xml',
        'data/mail_template_data.xml',
        'views/medical_website_backend_views.xml',
        'views/res_config_settings_views.xml',
        'views/medical_website_templates.xml',
        'views/medical_booking_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'medical_website/static/src/scss/medical_website.scss',
            'medical_website/static/src/js/booking.js',
        ],
        'web.assets_tests': [
            'medical_website/static/tests/tours/*.js',
        ],
    },
    'installable': True,
}
