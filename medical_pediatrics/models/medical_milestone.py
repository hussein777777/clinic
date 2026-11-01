from odoo import _, api, fields, models

DOMAINS = [
    ('gross_motor', 'Movement'),
    ('fine_motor', 'Hands'),
    ('language', 'Language'),
    ('social', 'Social & Emotional'),
    ('cognitive', 'Thinking'),
]


class MedicalMilestone(models.Model):
    _name = 'medical.milestone'
    _description = 'Developmental Milestone'
    _order = 'age_months, domain, sequence'

    name = fields.Char(required=True, translate=True)
    domain = fields.Selection(DOMAINS, required=True)
    age_months = fields.Integer(string='Most Children by (months)', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class MedicalMilestoneCheck(models.Model):
    _name = 'medical.milestone.check'
    _description = 'Milestone Check'
    _order = 'age_months, id'

    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade')
    milestone_id = fields.Many2one('medical.milestone', required=True)
    domain = fields.Selection(related='milestone_id.domain', store=True)
    age_months = fields.Integer(related='milestone_id.age_months', store=True)
    achieved = fields.Boolean()
    date = fields.Date(string='Checked on', default=fields.Date.context_today)
    status = fields.Selection([('done', 'Achieved'), ('pending', 'Not yet'), ('late', 'Not yet - discuss')],
                              compute='_compute_status')

    _patient_milestone_uniq = models.Constraint('UNIQUE(patient_id, milestone_id)', 'Milestone already checked.')

    @api.depends('achieved', 'age_months', 'patient_id.patient_age_months')
    def _compute_status(self):
        for check in self:
            if check.achieved:
                check.status = 'done'
            elif check.patient_id.patient_age_months > check.age_months + 2:
                check.status = 'late'
            else:
                check.status = 'pending'


class ResPartner(models.Model):
    _inherit = 'res.partner'

    milestone_check_ids = fields.One2many('medical.milestone.check', 'patient_id', string='Development')
    milestones_late = fields.Integer(compute='_compute_milestones_late')

    def _compute_milestones_late(self):
        for partner in self:
            partner.milestones_late = len(partner.milestone_check_ids.filtered(lambda c: c.status == 'late'))

    def action_update_milestones(self):
        """Add the milestones expected up to the child's age (plus the next ones)."""
        Milestone = self.env['medical.milestone']
        for partner in self.filtered('birthdate'):
            limit = partner.patient_age_months + 3
            missing = Milestone.search([('age_months', '<=', limit)]) - partner.milestone_check_ids.milestone_id
            self.env['medical.milestone.check'].create(
                [{'patient_id': partner.id, 'milestone_id': m.id} for m in missing])
        return True
