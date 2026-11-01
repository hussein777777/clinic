from odoo import api, fields, models

GROUPS = 'hr.group_hr_user,medical_core.group_medical_user'


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    is_practitioner = fields.Boolean(string='Doctor', groups=GROUPS)
    medical_department_ids = fields.Many2many('medical.department', 'medical_department_employee_rel',
                                              'employee_id', 'department_id', string='Clinics', groups=GROUPS)
    main_specialty_id = fields.Many2one('medical.specialty', string='Main Specialty', groups=GROUPS)
    default_room_id = fields.Many2one('medical.room', string='Default Room', groups=GROUPS)
    medical_license_no = fields.Char(string='License No.', groups=GROUPS)
    medical_title = fields.Char(string='Title on Documents', groups=GROUPS, translate=True,
                                help='Printed under the name on prescriptions, e.g. "Consultant Orthopedic Surgeon".')

    @api.onchange('main_specialty_id')
    def _onchange_main_specialty_id(self):
        department = self.main_specialty_id.department_id
        if department and department not in self.medical_department_ids:
            self.medical_department_ids |= department
