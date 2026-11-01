from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_insurer = fields.Boolean(string='Insurance Company / TPA')
    medical_insurance_plan_ids = fields.One2many('medical.insurance.plan', 'insurer_id', string='Insurance Plans')
    medical_policy_ids = fields.One2many('medical.patient.policy', 'patient_id', string='Insurance Policies')
