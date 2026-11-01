from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

CASE_TYPES = [
    ('fracture', 'Fracture'),
    ('dislocation', 'Dislocation'),
    ('sprain', 'Sprain / Strain'),
    ('ligament', 'Ligament / Tendon Injury'),
    ('degenerative', 'Degenerative (Osteoarthritis)'),
    ('spine', 'Back / Neck Problem'),
    ('sports', 'Sports Injury'),
    ('post_op', 'Post-operative Follow-up'),
    ('other', 'Other'),
]


class MedicalOrthopedicCase(models.Model):
    """One musculoskeletal problem followed from injury to recovery: imaging, cast, surgery, physiotherapy."""
    _name = 'medical.orthopedic.case'
    _description = 'Orthopedic Case'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'injury_date desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, tracking=True,
                                 domain=[('is_patient', '=', True)])
    doctor_id = fields.Many2one('hr.employee', string='Orthopedist', required=True, tracking=True,
                                domain=[('is_practitioner', '=', True)])
    case_type = fields.Selection(CASE_TYPES, required=True, default='fracture', tracking=True)
    body_site_id = fields.Many2one('medical.body.site', string='Site', required=True)
    laterality = fields.Selection([('right', 'Right'), ('left', 'Left'), ('bilateral', 'Both Sides')], string='Side')
    diagnosis_id = fields.Many2one('medical.diagnosis', string='Diagnosis')
    injury_date = fields.Date(string='Injury / Onset Date', default=fields.Date.context_today, required=True)
    mechanism = fields.Selection([
        ('fall', 'Fall'),
        ('road', 'Road Traffic Accident'),
        ('sports', 'Sports'),
        ('work', 'Work Accident'),
        ('overuse', 'Overuse'),
        ('none', 'No Trauma'),
        ('other', 'Other'),
    ], default='fall')
    # fracture details
    fracture_classification = fields.Char(help='e.g. AO/OTA 23-A2, Weber B, Garden III')
    is_open_fracture = fields.Boolean(string='Open Fracture')
    is_displaced = fields.Boolean(string='Displaced')
    neurovascular_ok = fields.Boolean(string='Neurovascular Status Intact', default=True)
    treatment = fields.Selection([
        ('conservative', 'Conservative'),
        ('cast', 'Cast / Splint'),
        ('reduction', 'Closed Reduction'),
        ('surgery', 'Surgery'),
        ('physio', 'Physiotherapy'),
        ('injection', 'Injection'),
    ], default='cast', tracking=True)
    expected_weeks = fields.Integer(string='Expected Healing (weeks)', default=6)
    expected_healed_date = fields.Date(compute='_compute_expected_healed_date', store=True)
    healed_date = fields.Date(tracking=True, copy=False)
    weight_bearing = fields.Selection([
        ('none', 'Non-weight-bearing'),
        ('touch', 'Toe-touch'),
        ('partial', 'Partial'),
        ('full', 'Full'),
    ], tracking=True)
    state = fields.Selection([
        ('acute', 'Acute'),
        ('healing', 'Healing'),
        ('rehab', 'Rehabilitation'),
        ('closed', 'Closed'),
    ], default='acute', required=True, tracking=True, index=True)
    visit_ids = fields.One2many('medical.visit', 'orthopedic_case_id', string='Visits')
    cast_ids = fields.One2many('medical.orthopedic.cast', 'case_id', string='Casts & Splints')
    surgery_ids = fields.One2many('medical.surgery', 'orthopedic_case_id', string='Operations')
    order_ids = fields.One2many('medical.order', 'orthopedic_case_id', string='Imaging')
    physio_contract_id = fields.Many2one('medical.care.contract', string='Physiotherapy Program', copy=False)
    visit_count = fields.Integer(compute='_compute_counts')
    weeks_since_injury = fields.Integer(compute='_compute_weeks')
    current_cast_id = fields.Many2one('medical.orthopedic.cast', compute='_compute_counts')
    note = fields.Html(groups='medical_core.group_medical_doctor')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    @api.depends('injury_date', 'expected_weeks')
    def _compute_expected_healed_date(self):
        for case in self:
            case.expected_healed_date = case.injury_date + relativedelta(weeks=case.expected_weeks) \
                if case.injury_date and case.expected_weeks else False

    def _compute_counts(self):
        for case in self:
            case.visit_count = len(case.visit_ids)
            case.current_cast_id = case.cast_ids.filtered(lambda c: c.state == 'on')[:1]

    @api.depends('injury_date')
    def _compute_weeks(self):
        today = fields.Date.context_today(self)
        for case in self:
            case.weeks_since_injury = (today - case.injury_date).days // 7 if case.injury_date else 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.orthopedic.case') or _('New')
        return super().create(vals_list)

    @api.depends('name', 'body_site_id', 'laterality', 'case_type')
    def _compute_display_name(self):
        types = dict(self._fields['case_type']._description_selection(self.env))
        sides = dict(self._fields['laterality']._description_selection(self.env))
        for case in self:
            parts = [types.get(case.case_type), case.body_site_id.name, sides.get(case.laterality)]
            case.display_name = f"{case.name} - {' '.join(p for p in parts if p)}"

    # ---------------------------------------------------------------- workflow
    def action_healing(self):
        self.filtered(lambda c: c.state == 'acute').write({'state': 'healing'})

    def action_rehab(self):
        self.filtered(lambda c: c.state in ('acute', 'healing')).write({'state': 'rehab'})

    def action_close(self):
        for case in self:
            if case.cast_ids.filtered(lambda c: c.state == 'on'):
                raise UserError(_("Remove the cast or splint before closing %s.", case.name))
        self.write({'state': 'closed', 'healed_date': fields.Date.context_today(self)})

    def action_reopen(self):
        self.write({'state': 'healing', 'healed_date': False})

    def _context_defaults(self):
        self.ensure_one()
        return {'default_orthopedic_case_id': self.id, 'default_patient_id': self.patient_id.id,
                'default_doctor_id': self.doctor_id.id, 'default_body_site_id': self.body_site_id.id,
                'default_laterality': self.laterality if self.laterality != 'bilateral' else False}

    def action_apply_cast(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.orthopedic.cast',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
            'context': dict(self._context_defaults(), default_case_id=self.id,
                            default_removal_due=fields.Date.context_today(self) + relativedelta(weeks=self.expected_weeks or 6)),
        }

    def action_book_visit(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.visit',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'context': dict(self._context_defaults(), default_visit_type='follow_up'),
        }

    def action_order_imaging(self):
        self.ensure_one()
        xray = self.env.ref('medical_core.product_img_xray', raise_if_not_found=False)
        order = self.env['medical.order'].create({
            'patient_id': self.patient_id.id,
            'doctor_id': self.doctor_id.id,
            'order_type': 'imaging',
            'orthopedic_case_id': self.id,
            'visit_id': self.visit_ids.filtered(lambda v: v.state in ('checked_in', 'in_consult'))[:1].id,
            'clinical_info': self.display_name,
            'line_ids': [(0, 0, {'product_id': xray.id, 'body_site_id': self.body_site_id.id,
                                 'laterality': self.laterality})] if xray else [],
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.order',
            'res_id': order.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_book_surgery(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.surgery',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'context': {'default_orthopedic_case_id': self.id, 'default_patient_id': self.patient_id.id,
                        'default_surgeon_id': self.doctor_id.id, 'default_body_site_id': self.body_site_id.id,
                        'default_laterality': self.laterality},
        }

    def action_start_physio(self):
        self.ensure_one()
        package = self.env.ref('medical_orthopedics.product_physio_package_10', raise_if_not_found=False)
        session = self.env.ref('medical_orthopedics.product_physio_session', raise_if_not_found=False)
        if not self.physio_contract_id:
            self.physio_contract_id = self.env['medical.care.contract'].create({
                'care_type': 'physiotherapy',
                'patient_id': self.patient_id.id,
                'doctor_id': self.doctor_id.id,
                'product_id': package.id,
                'session_product_id': session.id,
                'billing_mode': 'sessions',
                'session_count': 10,
                'est_months': 2,
                'orthopedic_case_id': self.id,
            })
        self.action_rehab()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.care.contract',
            'res_id': self.physio_contract_id.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_view_visits(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('medical_core.action_medical_visit_history')
        action.update({'domain': [('orthopedic_case_id', '=', self.id)], 'context': self._context_defaults()})
        return action

    def action_rom_trend(self):
        """Patient file, measurements tab: ROM and pain trends."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.observation',
            'view_mode': 'graph,list',
            'domain': [('patient_id', '=', self.patient_id.id), ('category', 'in', ('exam', 'score'))],
            'context': {'search_default_group_type': 1, 'graph_groupbys': ['date:week', 'type_id']},
            'name': _("Range of Motion & Scores"),
        }


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    orthopedic_case_id = fields.Many2one('medical.orthopedic.case', string='Orthopedic Case', index='btree_not_null',
                                         domain="[('patient_id', '=', patient_id), ('state', '!=', 'closed')]")


class MedicalOrder(models.Model):
    _inherit = 'medical.order'

    orthopedic_case_id = fields.Many2one('medical.orthopedic.case', string='Orthopedic Case', index='btree_not_null')


class MedicalSurgery(models.Model):
    _inherit = 'medical.surgery'

    orthopedic_case_id = fields.Many2one('medical.orthopedic.case', string='Orthopedic Case', index='btree_not_null')

    def action_done(self):
        res = super().action_done()
        self.orthopedic_case_id.filtered(lambda c: c.state == 'acute').action_healing()
        return res


class MedicalCareContract(models.Model):
    _inherit = 'medical.care.contract'

    care_type = fields.Selection(selection_add=[('physiotherapy', 'Physiotherapy')],
                                 ondelete={'physiotherapy': 'set default'})
    orthopedic_case_id = fields.Many2one('medical.orthopedic.case', string='Orthopedic Case', index='btree_not_null')
    home_exercises = fields.Html(string='Home Exercise Program', translate=False)

    def action_print_exercises(self):
        return self.env.ref('medical_orthopedics.action_report_home_exercises').report_action(self)
