{
    'name': 'Medical Center - Accounting',
    'version': '19.0.1.0.0',
    'category': 'Healthcare',
    'summary': 'Patient / insurer invoicing, advances, claim settlements, care program deferred revenue, '
               'doctor commissions, cash closing',
    'description': """
Medical Center Accounting
=========================
* Closing a visit posts the patient-share invoice (advance deductions included)
* Claim batches invoice the insurer; settlements post credit notes for rejections
* Rejections: re-bill the patient, write off, or resubmit
* Orthodontic package: contract invoice to deferred revenue with an installment payment term,
  monthly revenue recognition, early termination
* Optional month-end accrual of unbilled insurer revenue
* Doctor analytic accounts and commissions on collected revenue
* Daily cash closing per cashier and a sub-ledger reconciliation check
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_insurance', 'account'],
    'data': [
        'security/medical_account_security.xml',
        'security/ir.model.access.csv',
        'data/product_data.xml',
        'data/ir_cron_data.xml',
        'wizard/medical_advance_wizard_views.xml',
        'wizard/medical_commission_wizard_views.xml',
        'wizard/medical_reconciliation_check_views.xml',
        'views/res_config_settings_views.xml',
        'views/medical_visit_views.xml',
        'views/medical_claim_views.xml',
        'views/medical_care_contract_views.xml',
        'views/medical_commission_views.xml',
        'views/medical_cash_closing_views.xml',
        'views/account_move_views.xml',
        'views/medical_account_menus.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
}
