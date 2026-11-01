from odoo import api, fields, models, _


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    policy_id = fields.Many2one('medical.patient.policy', string='Insurance Policy',
                                compute='_compute_policy_id', store=True, readonly=False, precompute=True,
                                domain="[('patient_id', '=', patient_id)]", tracking=True)
    insurer_id = fields.Many2one(related='policy_id.insurer_id', store=True)
    amount_insurance = fields.Monetary(string='Insurer Share', compute='_compute_insurance_amounts', store=True)
    amount_patient = fields.Monetary(string='Patient Share', compute='_compute_insurance_amounts', store=True)

    @api.depends('patient_id', 'start')
    def _compute_policy_id(self):
        for visit in self:
            if visit.state in ('done', 'invoiced'):
                continue
            if not visit.policy_id or visit.policy_id.patient_id != visit.patient_id:
                visit.policy_id = self.env['medical.patient.policy']._get_patient_policy(
                    visit.patient_id, visit.start.date() if visit.start else fields.Date.context_today(visit))

    @api.depends('line_ids.insurance_amount', 'line_ids.patient_amount', 'line_ids.state')
    def _compute_insurance_amounts(self):
        for visit in self:
            lines = visit.line_ids.filtered(lambda l: l.state != 'cancelled')
            visit.amount_insurance = sum(lines.mapped('insurance_amount'))
            visit.amount_patient = sum(lines.mapped('patient_amount'))

    def write(self, vals):
        res = super().write(vals)
        if 'policy_id' in vals:
            lines = self.line_ids.filtered(lambda l: l.state in ('planned', 'scheduled'))
            lines._recompute_prices()
        return res

    def _cap_insurance_to_policy_limit(self):
        """Several lines of the same visit may together exceed the remaining annual limit."""
        for visit in self:
            policy = visit.policy_id
            cap = policy._get_remaining_cap() if policy else None
            if cap is None:
                continue
            for line in visit.line_ids.filtered(lambda l: l.insurance_amount > 0 and not l.claim_line_ids).sorted('id'):
                if line.currency_id.compare_amounts(line.insurance_amount, cap) > 0:
                    line.insurance_amount = cap
                cap -= line.insurance_amount

    def _create_claim_lines(self):
        vals_list = []
        for visit in self:
            for line in visit.line_ids.filtered(
                    lambda l: l.state == 'executed' and l.policy_id and not l.insurance_excluded
                    and not l.claim_line_ids and l.insurance_amount > 0):
                vals_list.append(line._prepare_claim_line_vals())
        return self.env['medical.claim.line'].sudo().create(vals_list)

    def action_done(self):
        self._cap_insurance_to_policy_limit()
        res = super().action_done()
        self._create_claim_lines()
        return res


class MedicalTreatmentPlan(models.Model):
    _inherit = 'medical.treatment.plan'

    policy_id = fields.Many2one('medical.patient.policy', string='Insurance Policy',
                                domain="[('patient_id', '=', patient_id)]", tracking=True)
    amount_insurance_est = fields.Monetary(string='Estimated Insurer Share', compute='_compute_insurance_estimate')
    amount_patient_est = fields.Monetary(string='Estimated Patient Share', compute='_compute_insurance_estimate')
    preauth_ids = fields.One2many('medical.preauth', 'plan_id', string='Pre-authorizations')
    preauth_count = fields.Integer(compute='_compute_insurance_estimate')
    needs_preauth = fields.Boolean(compute='_compute_insurance_estimate')

    @api.onchange('patient_id')
    def _onchange_patient_policy(self):
        if self.patient_id and not self.policy_id:
            self.policy_id = self.env['medical.patient.policy']._get_patient_policy(self.patient_id, self.date)

    @api.model_create_multi
    def create(self, vals_list):
        policy_model = self.env['medical.patient.policy']
        for vals in vals_list:
            if not vals.get('policy_id') and vals.get('patient_id'):
                patient = self.env['res.partner'].browse(vals['patient_id'])
                vals['policy_id'] = policy_model._get_patient_policy(
                    patient, fields.Date.to_date(vals.get('date')) or fields.Date.context_today(self)).id
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if 'policy_id' in vals:
            self.line_ids.filtered(lambda l: l.state == 'planned')._recompute_prices()
        return res

    @api.depends('line_ids.insurance_amount', 'line_ids.patient_amount', 'line_ids.requires_preauth',
                 'line_ids.preauth_line_id', 'preauth_ids')
    def _compute_insurance_estimate(self):
        for plan in self:
            lines = plan.line_ids.filtered(lambda l: l.state != 'cancelled')
            plan.amount_insurance_est = sum(lines.mapped('insurance_amount'))
            plan.amount_patient_est = sum(lines.mapped('patient_amount'))
            plan.preauth_count = len(plan.preauth_ids)
            plan.needs_preauth = any(l.requires_preauth and not l.preauth_line_id and l.state != 'executed'
                                     for l in lines)

    def action_request_preauth(self):
        self.ensure_one()
        if not self.policy_id:
            from odoo.exceptions import UserError
            raise UserError(_("Set the patient's insurance policy on the plan first."))
        lines = self.line_ids.filtered(
            lambda l: l.requires_preauth and not l.preauth_line_id and l.state in ('planned', 'scheduled')
            and not l.preauth_line_ids.filtered(lambda p: p.preauth_id.state in ('draft', 'sent')))
        if not lines:
            from odoo.exceptions import UserError
            raise UserError(_("No line of this plan needs a new pre-authorization."))
        preauth = self.env['medical.preauth'].create({
            'policy_id': self.policy_id.id,
            'plan_id': self.id,
            'line_ids': [(0, 0, {'treatment_line_id': l.id, 'requested_amount': l.insurance_amount}) for l in lines],
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.preauth',
            'res_id': preauth.id,
            'view_mode': 'form',
        }

    def action_view_preauths(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('medical_insurance.action_medical_preauth')
        action['domain'] = [('plan_id', '=', self.id)]
        return action
