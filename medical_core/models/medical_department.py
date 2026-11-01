from odoo import api, fields, models, _

ROOM_TYPES = [
    ('consultation', 'Consultation Room'),
    ('procedure', 'Procedure Room'),
    ('chair', 'Treatment Chair'),
    ('device', 'Device / Lab Station'),
    ('imaging', 'Imaging'),
    ('theatre', 'Operating Theatre'),
]


class MedicalDepartment(models.Model):
    _name = 'medical.department'
    _description = 'Clinic / Department'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help='Short code used in sequences and reports, e.g. DEN, ORT.')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    manager_id = fields.Many2one('hr.employee', string='Head of Department')
    specialty_ids = fields.One2many('medical.specialty', 'department_id', string='Specialties')
    room_ids = fields.One2many('medical.room', 'department_id', string='Rooms')
    practitioner_ids = fields.Many2many('hr.employee', 'medical_department_employee_rel', 'department_id', 'employee_id',
                                        string='Doctors')
    default_visit_duration = fields.Float(string='Default Visit Duration (h)', default=0.5)
    note = fields.Html()

    _code_uniq = models.Constraint('UNIQUE(code, company_id)', 'The department code must be unique.')


class MedicalSpecialty(models.Model):
    _name = 'medical.specialty'
    _description = 'Medical Specialty'
    _order = 'department_id, sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    department_id = fields.Many2one('medical.department', required=True, ondelete='restrict', index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code)', 'The specialty code must be unique.')

    @api.depends('name', 'department_id.name')
    def _compute_display_name(self):
        for specialty in self:
            if self.env.context.get('specialty_short') or not specialty.department_id:
                specialty.display_name = specialty.name
            else:
                specialty.display_name = f"{specialty.department_id.name} / {specialty.name}"


class MedicalRoom(models.Model):
    _name = 'medical.room'
    _description = 'Room, Chair or Device'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    room_type = fields.Selection(ROOM_TYPES, required=True, default='consultation')
    department_id = fields.Many2one('medical.department', string='Department', index=True,
                                    help='Leave empty for rooms shared by every department.')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
