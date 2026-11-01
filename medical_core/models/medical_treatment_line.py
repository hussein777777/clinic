from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .medical_body_site import LATERALITY


class MedicalTreatmentLine(models.Model):
    _name = 'medical.treatment.line'
    _description = 'Treatment Line'
    _order = 'plan_id, sequence, id'

    sequence = fields.Integer(default=10)
    plan_id = fields.Many2one('medical.treatment.plan', string='Treatment Plan', index=True, ondelete='cascade')
    visit_id = fields.Many2one('medical.visit', string='Visit', index=True, ondelete='set null')
    patient_id = fields.Many2one('res.partner', string='Patient', compute='_compute_patient_id',
                                 store=True, readonly=False, index=True, required=True, precompute=True,
                                 domain=[('is_patient', '=', True)])
    doctor_id = fields.Many2one('hr.employee', string='Doctor', compute='_compute_doctor_id',
                                store=True, readonly=False, precompute=True, domain=[('is_practitioner', '=', True)])
    product_id = fields.Many2one('product.product', string='Procedure', required=True,
                                 domain=[('is_medical_procedure', '=', True)])
    specialty_id = fields.Many2one(related='product_id.specialty_id', store=True, string='Specialty')
    department_id = fields.Many2one(related='product_id.medical_department_id', store=True, string='Department')
    procedure_type = fields.Selection(related='product_id.procedure_type', store=True)
    site_requirement = fields.Selection(related='product_id.site_requirement')
    body_site_id = fields.Many2one('medical.body.site', string='Body Site')
    laterality = fields.Selection(LATERALITY, string='Side')
    site_label = fields.Char(string='Site', compute='_compute_site', store=True,
                             help='Where the procedure is done: body site, side, tooth...')
    site_key = fields.Char(compute='_compute_site', store=True, index=True,
                           help='Technical key used to compare two procedures on the same site.')
    care_contract_id = fields.Many2one('medical.care.contract', string='Care Program', index='btree_not_null',
                                       ondelete='restrict',
                                       help='Session covered by a prepaid program: billed through the program, not per line.')
    consent_id = fields.Many2one('medical.consent', string='Consent', copy=False, readonly=True)
    company_id = fields.Many2one('res.company', compute='_compute_company_id', store=True, readonly=False, precompute=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    price_unit = fields.Monetary(string='Price', compute='_compute_price_unit', store=True, readonly=False,
                                 precompute=True)
    discount = fields.Float(string='Discount (%)', digits='Discount')
    price_subtotal = fields.Monetary(string='Amount', compute='_compute_price_subtotal', store=True)
    state = fields.Selection([
        ('planned', 'Planned'),
        ('scheduled', 'Scheduled'),
        ('executed', 'Executed'),
        ('cancelled', 'Cancelled'),
    ], default='planned', required=True, index=True, copy=False)
    date_executed = fields.Date(string='Executed On', copy=False, readonly=True)
    note = fields.Char()

    # ------------------------------------------------------------------ computes
    @api.depends('plan_id.patient_id', 'visit_id.patient_id')
    def _compute_patient_id(self):
        for line in self:
            line.patient_id = line.visit_id.patient_id or line.plan_id.patient_id or line.patient_id

    @api.depends('plan_id.doctor_id', 'visit_id.doctor_id')
    def _compute_doctor_id(self):
        for line in self:
            if line.state == 'executed':
                continue
            line.doctor_id = line.visit_id.doctor_id or line.plan_id.doctor_id or line.doctor_id

    @api.depends('plan_id.company_id', 'visit_id.company_id')
    def _compute_company_id(self):
        for line in self:
            line.company_id = line.visit_id.company_id or line.plan_id.company_id or line.company_id or self.env.company

    @api.depends('product_id', 'visit_id', 'plan_id', 'care_contract_id')
    def _compute_price_unit(self):
        for line in self:
            if line.state == 'executed':
                continue
            line.price_unit = line._get_medical_price()

    def _get_medical_base_price(self):
        """Base price of the procedure. medical_insurance applies the contracted pricelist here."""
        self.ensure_one()
        return self.product_id.lst_price if self.product_id else 0.0

    def _get_medical_price(self):
        """Final unit price. Specialty modules add surcharges (extra surfaces, both sides...) on top of the base."""
        self.ensure_one()
        if self.care_contract_id:
            return 0.0  # paid through the program
        return self._get_medical_base_price()

    def _recompute_prices(self):
        for line in self.filtered(lambda l: l.state in ('planned', 'scheduled')):
            line.price_unit = line._get_medical_price()

    @api.depends('price_unit', 'discount')
    def _compute_price_subtotal(self):
        for line in self:
            subtotal = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            line.price_subtotal = line.currency_id.round(subtotal) if line.currency_id else subtotal

    def _site_parts(self):
        """(label, key) describing where the procedure is done. Specialty modules extend it (teeth, eyes)."""
        self.ensure_one()
        sides = dict(self._fields['laterality']._description_selection(self.env))
        label = ' '.join(p for p in (self.body_site_id.name, sides.get(self.laterality)) if p)
        key = ':'.join(p for p in (self.body_site_id.code, self.laterality) if p)
        return label, (f"site:{key}" if key else '')

    @api.depends('body_site_id', 'laterality')
    def _compute_site(self):
        for line in self:
            line.site_label, line.site_key = line._site_parts()

    @api.depends('product_id', 'site_label')
    def _compute_display_name(self):
        for line in self:
            line.display_name = ' - '.join(p for p in (line.product_id.name, line.site_label) if p)

    # --------------------------------------------------------------- constraints
    @api.constrains('discount')
    def _check_discount(self):
        for line in self:
            if line.discount < 0 or line.discount > 100:
                raise ValidationError(_("Discount must be between 0 and 100%."))
            limit = line._get_discount_limit()
            if line.discount > limit:
                raise ValidationError(_("Your discount limit is %(limit)s%%. Ask a clinic manager.", limit=limit))

    def _get_discount_limit(self):
        user = self.env.user
        if user.has_group('medical_core.group_medical_manager'):
            return 100.0
        params = self.env['ir.config_parameter'].sudo()
        if user.has_group('medical_core.group_medical_doctor'):
            return float(params.get_param('medical_core.doctor_discount_limit', 10.0))
        return float(params.get_param('medical_core.user_discount_limit', 0.0))

    @api.constrains('care_contract_id', 'patient_id')
    def _check_care_contract(self):
        for line in self.filtered('care_contract_id'):
            if line.care_contract_id.patient_id != line.patient_id:
                raise ValidationError(_("The care program belongs to another patient."))

    # ---------------------------------------------------------------------- CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('visit_id') and vals.get('state', 'planned') == 'planned':
                vals['state'] = 'scheduled'
        return super().create(vals_list)

    def _protected_fields(self):
        return {'product_id', 'price_unit', 'discount', 'patient_id', 'body_site_id', 'laterality', 'care_contract_id'}

    def write(self, vals):
        if self._protected_fields() & set(vals) and any(l.state == 'executed' for l in self) \
                and not self.env.context.get('medical_force_write'):
            raise UserError(_("Executed treatment lines cannot be modified."))
        res = super().write(vals)
        if 'visit_id' in vals:
            self.filtered(lambda l: l.visit_id and l.state == 'planned').write({'state': 'scheduled'})
            self.filtered(lambda l: not l.visit_id and l.state == 'scheduled').write({'state': 'planned'})
        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_executed(self):
        if any(line.state == 'executed' for line in self):
            raise UserError(_("You cannot delete an executed treatment line."))

    # ------------------------------------------------------------------- actions
    def _site_missing(self):
        """Message when the procedure needs a site that is not recorded, else False."""
        self.ensure_one()
        requirement = self.product_id.site_requirement
        if requirement == 'side' and not self.laterality:
            return _("%(proc)s requires the side (left / right).", proc=self.product_id.display_name)
        if requirement == 'body_site':
            if not self.body_site_id:
                return _("%(proc)s requires the body site.", proc=self.product_id.display_name)
            if self.body_site_id.has_laterality and not self.laterality:
                return _("%(proc)s on %(site)s requires the side.", proc=self.product_id.display_name,
                         site=self.body_site_id.name)
        return False

    def _medical_check_before_execute(self):
        """Hook for specialty modules (lab delivery, pre-authorization...)."""
        for line in self:
            message = line._site_missing()
            if message:
                raise UserError(message)
            if line.product_id.requires_consent and not line.consent_id.signed:
                raise UserError(_("%(proc)s needs the patient's signed consent first.", proc=line.display_name))
            contract = line.care_contract_id
            if contract and contract.state != 'active':
                raise UserError(_("Care program %s is not active.", contract.name))
            if contract and contract.billing_mode == 'sessions' and contract.sessions_remaining <= 0:
                raise UserError(_("Care program %s has no sessions left.", contract.name))

    def action_execute(self):
        lines = self.filtered(lambda l: l.state in ('planned', 'scheduled'))
        lines._medical_check_before_execute()
        today = fields.Date.context_today(self)
        for line in lines:
            line.write({'state': 'executed', 'date_executed': line.visit_id.start.date() if line.visit_id.start else today})
        lines.plan_id._update_progress()
        lines._after_execute()
        return True

    def _after_execute(self):
        """Hook called once lines are executed (tooth chart, stock consumption, session revenue...)."""
        return True

    def action_cancel(self):
        if any(l.state == 'executed' for l in self):
            raise UserError(_("Executed lines cannot be cancelled."))
        self.write({'state': 'cancelled', 'visit_id': False})
        self.plan_id._update_progress()

    def action_reset_planned(self):
        self.filtered(lambda l: l.state == 'cancelled').write({'state': 'planned'})

    def action_consent(self):
        """Open (or create) the consent form of this line."""
        self.ensure_one()
        consent = self.consent_id
        if not consent:
            consent = self.env['medical.consent'].create({
                'patient_id': self.patient_id.id,
                'product_id': self.product_id.id,
                'doctor_id': self.doctor_id.id,
                'visit_id': self.visit_id.id,
            })
            self.with_context(medical_force_write=True).consent_id = consent
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.consent',
            'res_id': consent.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
        }
