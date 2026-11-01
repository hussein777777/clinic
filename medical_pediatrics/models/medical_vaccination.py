from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MedicalVaccine(models.Model):
    _name = 'medical.vaccine'
    _description = 'Vaccine'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    diseases = fields.Char(translate=True, help='What it protects against.')
    product_id = fields.Many2one('product.product', string='Billing Item',
                                 help='Procedure added to the visit when the dose is given (vaccine + administration).')
    route = fields.Selection([('im', 'Intramuscular'), ('sc', 'Subcutaneous'), ('id', 'Intradermal'), ('oral', 'Oral')],
                             default='im')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code)', 'The vaccine code must be unique.')


class MedicalVaccineSchedule(models.Model):
    """Default immunisation schedule. Edit it to follow the national schedule."""
    _name = 'medical.vaccine.schedule'
    _description = 'Immunisation Schedule Dose'
    _order = 'age_days, sequence, id'

    vaccine_id = fields.Many2one('medical.vaccine', required=True, ondelete='cascade')
    dose_label = fields.Char(string='Dose', required=True, translate=True, help='e.g. Dose 1, Booster')
    age_days = fields.Integer(string='Due at Age (days)', required=True)
    age_label = fields.Char(string='Age', compute='_compute_age_label')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    @api.depends('age_days')
    def _compute_age_label(self):
        for line in self:
            days = line.age_days
            if days == 0:
                line.age_label = _("At birth")
            elif days < 730:
                line.age_label = _("%s months", round(days / 30.4375))
            else:
                line.age_label = _("%s years", round(days / 365.25, 1))


class MedicalVaccination(models.Model):
    _name = 'medical.vaccination'
    _description = 'Vaccination'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'due_date, id'

    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade',
                                 domain=[('is_patient', '=', True)])
    vaccine_id = fields.Many2one('medical.vaccine', required=True, index=True)
    schedule_id = fields.Many2one('medical.vaccine.schedule', ondelete='set null')
    dose_label = fields.Char(string='Dose')
    due_date = fields.Date(index=True)
    date_given = fields.Date(tracking=True)
    state = fields.Selection([('planned', 'Planned'), ('given', 'Given'), ('skipped', 'Not Given')],
                             default='planned', required=True, tracking=True, index=True)
    is_overdue = fields.Boolean(compute='_compute_is_overdue', search='_search_is_overdue')
    lot_number = fields.Char(string='Lot / Batch')
    expiry_date = fields.Date(string='Vaccine Expiry')
    site = fields.Selection([('left_thigh', 'Left thigh'), ('right_thigh', 'Right thigh'), ('left_arm', 'Left arm'),
                             ('right_arm', 'Right arm'), ('oral', 'Oral')], string='Injection Site')
    given_by = fields.Many2one('res.users', string='Given by')
    visit_id = fields.Many2one('medical.visit', ondelete='set null')
    skip_reason = fields.Char()
    reaction = fields.Char(string='Reaction Observed')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    _patient_schedule_uniq = models.Constraint('UNIQUE(patient_id, schedule_id)', 'This dose is already planned.')

    @api.depends('due_date', 'state')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for vac in self:
            vac.is_overdue = vac.state == 'planned' and bool(vac.due_date) and vac.due_date < today

    def _search_is_overdue(self, operator, value):
        if operator in ('in', 'not in'):
            value = True in value
            operator = '=' if operator == 'in' else '!='
        domain = [('state', '=', 'planned'), ('due_date', '<', fields.Date.context_today(self))]
        return domain if (operator == '=') == bool(value) else ['!', '&', *domain]

    @api.depends('vaccine_id', 'dose_label')
    def _compute_display_name(self):
        for vac in self:
            vac.display_name = ' '.join(p for p in (vac.vaccine_id.name, vac.dose_label) if p)

    def action_give(self):
        today = fields.Date.context_today(self)
        for vac in self:
            if vac.state == 'given':
                raise UserError(_("%s was already given.", vac.display_name))
            if vac.expiry_date and vac.expiry_date < today:
                raise UserError(_("The vaccine lot %s is expired.", vac.lot_number or ''))
            vals = {'state': 'given', 'date_given': vac.date_given or today, 'given_by': vac.given_by.id or self.env.uid}
            visit = vac.visit_id or vac.patient_id.medical_visit_ids.filtered(
                lambda v: v.state in ('checked_in', 'in_consult'))[:1]
            if visit and vac.vaccine_id.product_id:
                self.env['medical.treatment.line'].create({
                    'visit_id': visit.id, 'product_id': vac.vaccine_id.product_id.id,
                    'note': vac.display_name})
                vals['visit_id'] = visit.id
            vac.write(vals)
            vac.activity_ids.action_feedback(feedback=_("Given"))
        return True

    def action_skip(self):
        self.write({'state': 'skipped'})
        self.activity_ids.unlink()

    def action_reset(self):
        self.write({'state': 'planned', 'date_given': False})

    @api.model
    def _cron_vaccination_reminders(self):
        """Daily: one reminder per dose that became due, for the reception to call the family."""
        today = fields.Date.context_today(self)
        due = self.search([('state', '=', 'planned'), ('due_date', '<=', today + timedelta(days=3)),
                           ('activity_ids', '=', False)])
        user = self.env.ref('base.user_admin')
        for vac in due:
            vac.activity_schedule('mail.mail_activity_data_call', date_deadline=vac.due_date, user_id=user.id,
                                  summary=_("Vaccine due: %(vac)s - %(child)s", vac=vac.display_name,
                                            child=vac.patient_id.name))


class ResPartner(models.Model):
    _inherit = 'res.partner'

    vaccination_ids = fields.One2many('medical.vaccination', 'patient_id', string='Vaccinations')
    vaccines_overdue = fields.Integer(compute='_compute_vaccines_overdue')

    def _compute_vaccines_overdue(self):
        today = fields.Date.context_today(self)
        for partner in self:
            partner.vaccines_overdue = len(partner.vaccination_ids.filtered(
                lambda v: v.state == 'planned' and v.due_date and v.due_date < today))

    def action_generate_vaccination_plan(self):
        """Plan every dose of the schedule from the date of birth (doses already planned are kept)."""
        Vaccination = self.env['medical.vaccination']
        schedule = self.env['medical.vaccine.schedule'].search([])
        for partner in self:
            if not partner.birthdate:
                raise UserError(_("Enter the date of birth of %s first.", partner.name))
            planned = partner.vaccination_ids.schedule_id
            Vaccination.create([{
                'patient_id': partner.id,
                'vaccine_id': line.vaccine_id.id,
                'schedule_id': line.id,
                'dose_label': line.dose_label,
                'due_date': partner.birthdate + timedelta(days=line.age_days),
            } for line in schedule - planned])
        return True

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        auto = self.env['ir.config_parameter'].sudo().get_param('medical_pediatrics.auto_vaccination_plan', 'True')
        if auto not in ('False', '0'):
            today = fields.Date.context_today(self)
            babies = partners.filtered(lambda p: p.is_patient and p.birthdate and (today - p.birthdate).days < 7 * 365)
            babies.sudo().action_generate_vaccination_plan()
        return partners
