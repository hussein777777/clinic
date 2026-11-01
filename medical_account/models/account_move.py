from odoo import api, fields, models

MEDICAL_MOVE_KINDS = [
    ('patient', 'Patient Invoice'),
    ('advance', 'Patient Advance'),
    ('rebill', 'Insurance Rejection Re-bill'),
    ('insurer', 'Insurer Invoice'),
    ('insurer_refund', 'Insurer Rejection Credit Note'),
    ('program_contract', 'Care Program Contract'),
    ('program_recognition', 'Program Revenue Recognition'),
    ('program_termination', 'Program Termination'),
    ('accrual', 'Insurance Accrual'),
    ('writeoff', 'Insurance Write-off'),
    ('commission', 'Doctor Commission'),
    ('cash_diff', 'Cash Difference'),
]


class AccountMove(models.Model):
    _inherit = 'account.move'

    medical_visit_id = fields.Many2one('medical.visit', string='Medical Visit', index='btree_not_null', copy=False)
    medical_claim_id = fields.Many2one('medical.claim', string='Insurance Claim', index='btree_not_null', copy=False)
    care_contract_id = fields.Many2one('medical.care.contract', string='Care Program',
                                       index='btree_not_null', copy=False)
    medical_move_kind = fields.Selection(MEDICAL_MOVE_KINDS, string='Medical Document', copy=False, index=True)

    def _compute_payment_state(self):
        super()._compute_payment_state()
        claims = self.filtered(lambda m: m.medical_claim_id and m.medical_move_kind == 'insurer'
                               and m.payment_state in ('paid', 'in_payment')).medical_claim_id
        claims.filtered(lambda c: c.state == 'settled').sudo().write({'state': 'paid'})


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    medical_line_id = fields.Many2one('medical.treatment.line', string='Medical Treatment',
                                     index='btree_not_null', copy=True)
    medical_claim_line_id = fields.Many2one('medical.claim.line', string='Claim Line', index='btree_not_null', copy=True)
    medical_doctor_id = fields.Many2one('hr.employee', string='Doctor', index='btree_not_null', copy=True)
    medical_plan_id = fields.Many2one('medical.treatment.plan', string='Treatment Plan (advance)',
                                     index='btree_not_null', copy=True)
