from dateutil.relativedelta import relativedelta

from odoo import fields, models, _
from odoo.exceptions import UserError


def _default_date_to(self):
    today = fields.Date.context_today(self)
    return today.replace(day=1) - relativedelta(days=1)


def _default_date_from(self):
    return _default_date_to(self).replace(day=1)


class MedicalClaimBatchWizard(models.TransientModel):
    _name = 'medical.claim.batch.wizard'
    _description = 'Create monthly insurance claims'

    date_from = fields.Date(default=_default_date_from)
    date_to = fields.Date(required=True, default=_default_date_to)
    insurer_ids = fields.Many2many('res.partner', string='Insurers', domain=[('is_insurer', '=', True)],
                                   help='Leave empty for all insurers.')

    def action_create(self):
        claims = self.env['medical.claim']._create_batches(self.date_from, self.date_to, self.insurer_ids or None)
        if not claims:
            raise UserError(_("Nothing to claim for this period."))
        action = self.env['ir.actions.act_window']._for_xml_id('medical_insurance.action_medical_claim')
        action['domain'] = [('id', 'in', claims.ids)]
        action['context'] = {}
        return action
