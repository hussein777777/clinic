from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

CONSUMING_STATES = ('to_claim', 'claimed', 'approved', 'partially_approved')


class MedicalPatientPolicy(models.Model):
    _name = 'medical.patient.policy'
    _description = 'Patient Insurance Policy'
    _inherit = ['mail.thread']
    _order = 'date_to desc, id desc'

    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade',
                                 domain=[('is_patient', '=', True)], tracking=True)
    plan_id = fields.Many2one('medical.insurance.plan', required=True, index=True, tracking=True)
    insurer_id = fields.Many2one(related='plan_id.insurer_id', store=True, index=True)
    member_no = fields.Char(string='Member / Card No.', required=True, tracking=True)
    date_from = fields.Date(required=True, tracking=True)
    date_to = fields.Date(required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(related='plan_id.company_id', store=True)
    currency_id = fields.Many2one(related='plan_id.currency_id')
    annual_limit = fields.Monetary(related='plan_id.annual_limit')
    used_amount = fields.Monetary(compute='_compute_used_amount')
    remaining_limit = fields.Monetary(compute='_compute_used_amount',
                                      help='Annual limit minus the insurer share already consumed in this policy year (0 when unlimited).')
    claim_line_ids = fields.One2many('medical.claim.line', 'policy_id')

    _member_plan_uniq = models.Constraint('UNIQUE(plan_id, member_no, date_from)',
                                          'This member already has a policy starting on that date for this plan.')

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for policy in self:
            if policy.date_to < policy.date_from:
                raise ValidationError(_("The policy end date must be after its start date."))

    @api.depends('claim_line_ids.claimed', 'claim_line_ids.approved', 'claim_line_ids.state', 'annual_limit')
    def _compute_used_amount(self):
        for policy in self:
            used = 0.0
            for line in policy.claim_line_ids.filtered(lambda l: l.state in CONSUMING_STATES):
                used += line.approved if line.state in ('approved', 'partially_approved') else line.claimed
            policy.used_amount = used
            policy.remaining_limit = max(policy.annual_limit - used, 0.0) if policy.annual_limit else 0.0

    def _compute_display_name(self):
        for policy in self:
            policy.display_name = f"{policy.insurer_id.name or ''} / {policy.member_no or ''}"

    def _get_remaining_cap(self):
        """Remaining insurer budget, None when the plan has no annual limit."""
        self.ensure_one()
        return self.remaining_limit if self.annual_limit else None

    def is_valid_on(self, date):
        self.ensure_one()
        return bool(self.active and date and self.date_from <= date <= self.date_to)

    @api.model
    def _get_patient_policy(self, patient, date):
        if not patient or not date:
            return self.browse()
        return self.search([
            ('patient_id', '=', patient.id),
            ('date_from', '<=', date),
            ('date_to', '>=', date),
        ], limit=1, order='date_from desc')
