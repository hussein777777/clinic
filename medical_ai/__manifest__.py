{
    'name': 'Medical Center - AI Assistant',
    'version': '19.0.1.0.0',
    'category': 'Healthcare',
    'summary': 'Claim scrubber, no-show risk, pre-visit briefs, treatment follow-up messages and appeal letters',
    'description': """
Medical Center AI Assistant
===========================
Works without any AI provider:
* Claim scrubber: rejection risk per claim line, from rule checks and this clinic's own history
* No-show risk: a transparent, self-calibrating estimate per booking, with a back-test
* Treatment follow-up list: accepted treatments never booked, ranked by value

With an AI provider (OpenAI, Anthropic, Google Gemini or any OpenAI-compatible server):
* Pre-visit brief for the doctor
* Personalised follow-up messages (WhatsApp ready)
* Appeal letters for rejected claims

Patient names, phones and IDs are replaced by placeholders before anything leaves Odoo.
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_account'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_config_parameter_data.xml',
        'data/ir_cron_data.xml',
        'views/res_config_settings_views.xml',
        'views/medical_ai_log_views.xml',
        'views/medical_claim_views.xml',
        'views/medical_visit_views.xml',
        'views/medical_plan_views.xml',
        'views/medical_ai_menus.xml',
    ],
    'installable': True,
}
