from odoo import api, fields, models, _
from odoo.exceptions import ValidationError



class MedicalInsurancePlan(models.Model):
    _name = 'medical.insurance.plan'
    _description = 'Medical Insurance Plan'
    _inherit = ['mail.thread']
    _order = 'insurer_id, name'

    name = fields.Char(required=True, tracking=True)
    insurer_id = fields.Many2one('res.partner', string='Insurer / TPA', required=True, index=True,
                                 domain=[('is_insurer', '=', True)], tracking=True)
    pricelist_id = fields.Many2one('product.pricelist', string='Contracted Prices',
                                   help='Prices agreed with the insurer. Leave empty to use the clinic price list.')
    rule_ids = fields.One2many('medical.insurance.rule', 'plan_id', string='Coverage Rules', copy=True)
    annual_limit = fields.Monetary(string='Annual Limit per Member', tracking=True,
                                   help='0 means no limit.')
    preauth_threshold = fields.Monetary(string='Pre-authorization above',
                                        help='Any procedure priced above this amount needs pre-authorization. 0 = rules only.')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    active = fields.Boolean(default=True)
    policy_count = fields.Integer(compute='_compute_policy_count')
    note = fields.Html(string='Contract Notes')

    def _compute_policy_count(self):
        data = dict(self.env['medical.patient.policy']._read_group([('plan_id', 'in', self.ids)], ['plan_id'], ['__count']))
        for plan in self:
            plan.policy_count = data.get(plan, 0)

    def _compute_display_name(self):
        for plan in self:
            plan.display_name = f"{plan.insurer_id.name} - {plan.name}" if plan.insurer_id else plan.name

    def _match_rule(self, product):
        """Most specific rule wins: product, category (nearest parent), specialty, department, then catch-all."""
        self.ensure_one()
        rules = self.rule_ids.sorted('sequence')
        rule = rules.filtered(lambda r: r.product_id and r.product_id == product)[:1]
        if rule:
            return rule
        categ = product.categ_id
        while categ:
            rule = rules.filtered(lambda r: not r.product_id and r.categ_id == categ)[:1]
            if rule:
                return rule
            categ = categ.parent_id
        generic = rules.filtered(lambda r: not r.product_id and not r.categ_id)
        rule = generic.filtered(lambda r: r.specialty_id and r.specialty_id == product.specialty_id)[:1]
        if rule:
            return rule
        rule = generic.filtered(lambda r: not r.specialty_id and r.department_id
                                and r.department_id == product.medical_department_id)[:1]
        if rule:
            return rule
        return generic.filtered(lambda r: not r.specialty_id and not r.department_id)[:1]

    def action_view_policies(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('medical_insurance.action_medical_patient_policy')
        action['domain'] = [('plan_id', '=', self.id)]
        action['context'] = {'default_plan_id': self.id}
        return action


class MedicalInsuranceRule(models.Model):
    _name = 'medical.insurance.rule'
    _description = 'Medical Insurance Coverage Rule'
    _order = 'sequence, id'

    plan_id = fields.Many2one('medical.insurance.plan', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', string='Procedure', domain=[('is_medical_procedure', '=', True)])
    categ_id = fields.Many2one('product.category', string='Category')
    specialty_id = fields.Many2one('medical.specialty', string='Specialty')
    department_id = fields.Many2one('medical.department', string='Clinic')
    excluded = fields.Boolean(string='Not Covered')
    coverage_pct = fields.Float(string='Coverage (%)', default=80.0)
    max_per_item = fields.Monetary(string='Max per Procedure', help='0 = no cap.')
    requires_preauth = fields.Boolean(string='Needs Pre-authorization')
    waiting_days = fields.Integer(help='Days after the policy start before this rule applies.')
    frequency_months = fields.Integer(string='Once every (months)',
                                      help='e.g. 6 for scaling: a second one within 6 months is not covered. 0 = no limit.')
    currency_id = fields.Many2one(related='plan_id.currency_id')

    @api.constrains('coverage_pct')
    def _check_coverage(self):
        for rule in self:
            if not 0 <= rule.coverage_pct <= 100:
                raise ValidationError(_("Coverage must be between 0 and 100%."))

    def _compute_display_name(self):
        for rule in self:
            target = rule.product_id.name or rule.categ_id.complete_name or rule.specialty_id.display_name \
                or rule.department_id.name or _("All procedures")
            rule.display_name = f"{target}: " + (_("not covered") if rule.excluded else f"{rule.coverage_pct:g}%")
