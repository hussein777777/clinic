from dateutil.relativedelta import relativedelta

from odoo import fields, models


class MedicalCommissionWizard(models.TransientModel):
    _name = 'medical.commission.wizard'
    _description = 'Compute doctor commissions'

    def _default_date_to(self):
        return fields.Date.context_today(self).replace(day=1) - relativedelta(days=1)

    date_from = fields.Date(required=True, default=lambda self: self._default_date_to().replace(day=1))
    date_to = fields.Date(required=True, default=_default_date_to)
    doctor_ids = fields.Many2many('hr.employee', string='Doctors', domain=[('is_practitioner', '=', True)],
                                  help='Leave empty for all doctors.')

    def action_compute(self):
        lines = self.env['medical.commission.line']._compute_commissions(
            self.date_from, self.date_to, self.doctor_ids or None)
        action = self.env['ir.actions.act_window']._for_xml_id('medical_account.action_medical_commission_line')
        action['domain'] = [('date_from', '=', self.date_from), ('date_to', '=', self.date_to)]
        action['context'] = {'search_default_group_doctor': 1}
        return action
