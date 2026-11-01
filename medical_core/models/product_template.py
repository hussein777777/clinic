from odoo import api, fields, models

PROCEDURE_TYPES = [
    ('consultation', 'Consultation'),
    ('procedure', 'Procedure'),
    ('session', 'Therapy Session'),
    ('lab', 'Lab Test'),
    ('imaging', 'Imaging'),
    ('test', 'Other Diagnostic Test'),
    ('surgery', 'Surgery'),
    ('package', 'Care Package'),
]

SITE_REQUIREMENTS = [
    ('none', 'None'),
    ('side', 'Side (left / right)'),
    ('body_site', 'Body Site'),
]

DOSAGE_FORMS = [
    ('tablet', 'Tablet'),
    ('capsule', 'Capsule'),
    ('syrup', 'Syrup / Suspension'),
    ('drops', 'Drops'),
    ('injection', 'Injection'),
    ('cream', 'Cream / Ointment'),
    ('gel', 'Gel'),
    ('inhaler', 'Inhaler'),
    ('suppository', 'Suppository'),
    ('patch', 'Patch'),
    ('other', 'Other'),
]

ROUTES = [
    ('oral', 'Oral'),
    ('topical', 'Topical'),
    ('eye', 'Eye'),
    ('ear', 'Ear'),
    ('nasal', 'Nasal'),
    ('inhalation', 'Inhalation'),
    ('im', 'Intramuscular'),
    ('iv', 'Intravenous'),
    ('sc', 'Subcutaneous'),
    ('rectal', 'Rectal'),
    ('vaginal', 'Vaginal'),
    ('sublingual', 'Sublingual'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # ---------------------------------------------------------------- procedures
    is_medical_procedure = fields.Boolean(string='Medical Procedure')
    specialty_id = fields.Many2one('medical.specialty', string='Specialty', index=True)
    medical_department_id = fields.Many2one(related='specialty_id.department_id', store=True, string='Department')
    procedure_type = fields.Selection(PROCEDURE_TYPES, default='procedure')
    site_requirement = fields.Selection(SITE_REQUIREMENTS, default='none', required=True,
                                        help='What the doctor must record on the treatment line before it is executed.')
    procedure_duration = fields.Float(string='Default Duration (min)', default=30)
    insurance_code = fields.Char(string='Insurance Code', help='Procedure code used on insurance claims.')
    multi_stage = fields.Boolean(string='Multi-stage', help='Executed over several visits.')
    requires_consent = fields.Boolean(string='Needs Signed Consent')
    consent_text = fields.Html(string='Consent Text', translate=True,
                               help='Shown to the patient before signing. Leave empty for the generic text.')
    result_type_ids = fields.Many2many('medical.observation.type', string='Result Components',
                                       help='Lab tests and measurements: results entered on the order become observations.')

    # ----------------------------------------------------------------- medicines
    is_medicine = fields.Boolean(string='Medicine')
    active_ingredient = fields.Char(help='Generic name, printed on prescriptions.')
    strength = fields.Char(help='e.g. 500 mg, 250 mg/5 ml')
    dosage_form = fields.Selection(DOSAGE_FORMS)
    default_route = fields.Selection(ROUTES, string='Route')
    allergen_ids = fields.Many2many('medical.allergen', string='Allergy Classes',
                                    help='Prescribing warns when the patient is allergic to one of these.')

    @api.onchange('is_medical_procedure')
    def _onchange_is_medical_procedure(self):
        if self.is_medical_procedure:
            self.type = 'service'
            self.sale_ok = True
