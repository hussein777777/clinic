from odoo import api, fields, models


class MedicalPhysioModality(models.Model):
    _name = 'medical.physio.modality'
    _description = 'Physiotherapy Modality'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    color = fields.Integer()


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    is_physio = fields.Boolean(compute='_compute_is_physio')
    physio_modality_ids = fields.Many2many('medical.physio.modality', string='Modalities Used')
    physio_exercises = fields.Text(string='Exercises Done')
    pain_after = fields.Integer(string='Pain After Session (0-10)')
    session_tolerance = fields.Selection([('good', 'Well tolerated'), ('fair', 'Some discomfort'),
                                          ('poor', 'Poorly tolerated')], string='Tolerance')

    @api.depends('department_id', 'care_contract_id.care_type', 'visit_type')
    def _compute_is_physio(self):
        physio = self.env.ref('medical_orthopedics.dept_physiotherapy', raise_if_not_found=False)
        for visit in self:
            visit.is_physio = visit.care_contract_id.care_type == 'physiotherapy' or \
                (physio and visit.department_id == physio)

    def action_done(self):
        res = super().action_done()
        pain = self.env.ref('medical_core.obs_pain', raise_if_not_found=False)
        for visit in self.filtered(lambda v: v.is_physio and v.pain_after and pain):
            self.env['medical.observation'].sudo().create({
                'patient_id': visit.patient_id.id, 'type_id': pain.id, 'value': visit.pain_after,
                'date': visit.stop or visit.start, 'visit_id': visit.id, 'source': 'exam',
                'note': visit.name,
            })
        return res
