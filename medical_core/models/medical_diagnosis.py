from odoo import api, fields, models, _

PROBLEM_STATES = [
    ('active', 'Active'),
    ('controlled', 'Controlled'),
    ('resolved', 'Resolved'),
]


class MedicalDiagnosis(models.Model):
    _name = 'medical.diagnosis'
    _description = 'Diagnosis (ICD-10)'
    _order = 'code'
    _rec_names_search = ['code', 'name']

    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True, translate=True)
    chapter = fields.Char(help='ICD-10 chapter or block, informative only.')
    department_ids = fields.Many2many('medical.department', string='Departments',
                                      help='Departments that use this code most; used to sort suggestions.')
    is_chronic = fields.Boolean(string='Chronic', help='Added to the patient problem list when diagnosed.')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code)', 'The diagnosis code must be unique.')

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for diagnosis in self:
            diagnosis.display_name = f"{diagnosis.code} {diagnosis.name}"


class MedicalAllergen(models.Model):
    _name = 'medical.allergen'
    _description = 'Allergen'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    kind = fields.Selection([('drug', 'Medicine'), ('food', 'Food'), ('other', 'Other')], default='drug', required=True)
    color = fields.Integer()

    _name_uniq = models.Constraint('UNIQUE(name)', 'This allergen already exists.')


class MedicalVisitDiagnosis(models.Model):
    _name = 'medical.visit.diagnosis'
    _description = 'Visit Diagnosis'
    _order = 'is_primary desc, id'

    visit_id = fields.Many2one('medical.visit', required=True, ondelete='cascade', index=True)
    patient_id = fields.Many2one(related='visit_id.patient_id', store=True, index=True)
    date = fields.Datetime(related='visit_id.start', store=True)
    doctor_id = fields.Many2one(related='visit_id.doctor_id', store=True)
    department_id = fields.Many2one(related='visit_id.department_id', store=True)
    diagnosis_id = fields.Many2one('medical.diagnosis', required=True, index=True)
    is_primary = fields.Boolean(string='Primary')
    certainty = fields.Selection([('provisional', 'Provisional'), ('confirmed', 'Confirmed')],
                                 default='confirmed', required=True)
    laterality = fields.Selection([('right', 'Right'), ('left', 'Left'), ('bilateral', 'Both Sides')])
    note = fields.Char()


class MedicalPatientProblem(models.Model):
    _name = 'medical.patient.problem'
    _description = 'Patient Problem (long-term condition)'
    _order = 'state, onset_date desc, id desc'

    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade',
                                 domain=[('is_patient', '=', True)])
    diagnosis_id = fields.Many2one('medical.diagnosis', index=True)
    name = fields.Char(required=True, compute='_compute_name', store=True, readonly=False, precompute=True)
    state = fields.Selection(PROBLEM_STATES, default='active', required=True)
    onset_date = fields.Date(string='Since')
    department_id = fields.Many2one('medical.department', string='Followed by')
    note = fields.Char()

    @api.depends('diagnosis_id')
    def _compute_name(self):
        for problem in self:
            if problem.diagnosis_id:
                problem.name = problem.diagnosis_id.display_name
