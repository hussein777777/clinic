from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import format_date

# WHO 2016 antenatal care model: 8 contacts at these gestational weeks
ANC_WEEKS = (12, 20, 26, 30, 34, 36, 38, 40)


def anc_focus(env, week):
    """What each WHO contact focuses on, in the user's language."""
    return {
        12: env._("Booking: history, blood tests, blood group and Rh, dating scan"),
        20: env._("Anomaly scan (18-22 weeks)"),
        26: env._("Glucose screening (24-28 weeks), blood pressure"),
        30: env._("Haemoglobin, fetal growth"),
        34: env._("Fetal growth, blood pressure, urine protein"),
        36: env._("Presentation, birth plan"),
        38: env._("Fetal well-being, signs of labour"),
        40: env._("Plan for post-term pregnancy"),
    }.get(week, '')


DIPSTICK = [('neg', 'Negative'), ('trace', 'Trace'), ('1', '+'), ('2', '++'), ('3', '+++')]


def ga_label(env, days):
    if days is None or days < 0:
        return ''
    return env._("%(w)sw %(d)sd", w=days // 7, d=days % 7)


class MedicalPregnancyRisk(models.Model):
    _name = 'medical.pregnancy.risk'
    _description = 'Pregnancy Risk Factor'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    is_high_risk = fields.Boolean(string='High Risk', default=True)
    sequence = fields.Integer(default=10)
    color = fields.Integer()


class MedicalPregnancy(models.Model):
    _name = 'medical.pregnancy'
    _description = 'Pregnancy'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'edd desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, tracking=True,
                                 domain=[('is_patient', '=', True), ('gender', '!=', 'male')])
    doctor_id = fields.Many2one('hr.employee', string='Obstetrician', domain=[('is_practitioner', '=', True)], tracking=True)
    lmp = fields.Date(string='Last Menstrual Period', tracking=True)
    edd_lmp = fields.Date(string='EDD by LMP', compute='_compute_edd_lmp', store=True)
    edd_us = fields.Date(string='EDD by Ultrasound', tracking=True)
    edd = fields.Date(string='Expected Delivery Date', compute='_compute_edd', store=True, tracking=True)
    ga_days = fields.Integer(string='Gestational Age (days)', compute='_compute_ga')
    ga_label = fields.Char(string='Gestational Age', compute='_compute_ga')
    trimester = fields.Selection([('1', 'First'), ('2', 'Second'), ('3', 'Third')], compute='_compute_ga')
    gravida = fields.Integer(help='Number of pregnancies including this one')
    para = fields.Integer(help='Births after 24 weeks')
    abortions = fields.Integer(string='Miscarriages / Abortions')
    living = fields.Integer(string='Living Children')
    fetuses = fields.Integer(string='Number of Fetuses', default=1)
    blood_group = fields.Selection(related='patient_id.blood_group')
    risk_factor_ids = fields.Many2many('medical.pregnancy.risk', string='Risk Factors')
    risk_level = fields.Selection([('low', 'Low Risk'), ('high', 'High Risk')], compute='_compute_risk_level', store=True)
    state = fields.Selection([('active', 'Ongoing'), ('delivered', 'Delivered'), ('loss', 'Pregnancy Loss'),
                              ('closed', 'Transferred / Closed')], default='active', required=True, tracking=True, index=True)
    contact_ids = fields.One2many('medical.antenatal.contact', 'pregnancy_id', string='Antenatal Contacts')
    visit_ids = fields.One2many('medical.visit', 'pregnancy_id', string='Visits')
    ultrasound_ids = fields.One2many('medical.obstetric.us', 'pregnancy_id', string='Ultrasounds')
    next_contact_id = fields.Many2one('medical.antenatal.contact', compute='_compute_next_contact')
    # outcome
    delivery_date = fields.Date(tracking=True)
    delivery_mode = fields.Selection([('svd', 'Normal vaginal delivery'), ('assisted', 'Assisted vaginal delivery'),
                                      ('cs_elective', 'Caesarean section (planned)'),
                                      ('cs_emergency', 'Caesarean section (emergency)')], tracking=True)
    birth_weight_g = fields.Integer(string='Birth Weight (g)')
    apgar_1 = fields.Integer(string='APGAR 1 min')
    apgar_5 = fields.Integer(string='APGAR 5 min')
    baby_patient_id = fields.Many2one('res.partner', string='Baby', domain=[('is_patient', '=', True)])
    outcome_note = fields.Text()
    care_contract_id = fields.Many2one('medical.care.contract', string='Maternity Package')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    @api.depends('lmp')
    def _compute_edd_lmp(self):
        for preg in self:
            preg.edd_lmp = preg.lmp + timedelta(days=280) if preg.lmp else False

    @api.depends('edd_lmp', 'edd_us')
    def _compute_edd(self):
        for preg in self:
            preg.edd = preg.edd_us or preg.edd_lmp

    @api.depends('edd', 'state', 'delivery_date')
    def _compute_ga(self):
        today = fields.Date.context_today(self)
        for preg in self:
            ref = preg.delivery_date if preg.state == 'delivered' and preg.delivery_date else today
            days = 280 - (preg.edd - ref).days if preg.edd else None
            preg.ga_days = days or 0
            preg.ga_label = ga_label(self.env, days) if days is not None else False
            if days is None:
                preg.trimester = False
            elif days < 14 * 7:
                preg.trimester = '1'
            elif days < 28 * 7:
                preg.trimester = '2'
            else:
                preg.trimester = '3'

    @api.depends('risk_factor_ids.is_high_risk', 'fetuses')
    def _compute_risk_level(self):
        for preg in self:
            preg.risk_level = 'high' if any(preg.risk_factor_ids.mapped('is_high_risk')) or preg.fetuses > 1 else 'low'

    def _compute_next_contact(self):
        for preg in self:
            preg.next_contact_id = preg.contact_ids.filtered(lambda c: c.state == 'planned').sorted('due_date')[:1]

    @api.constrains('lmp', 'edd_us')
    def _check_dates(self):
        today = fields.Date.context_today(self)
        for preg in self:
            if preg.lmp and preg.lmp > today:
                raise ValidationError(_("The last menstrual period cannot be in the future."))

    @api.depends('name', 'patient_id', 'edd')
    def _compute_display_name(self):
        for preg in self:
            preg.display_name = f"{preg.name} - {preg.patient_id.name or ''}" + (
                f" ({_('EDD')} {preg.edd})" if preg.edd else '')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.pregnancy') or _('New')
        pregnancies = super().create(vals_list)
        pregnancies._plan_contacts()
        return pregnancies

    def write(self, vals):
        res = super().write(vals)
        if {'lmp', 'edd_us'} & set(vals):
            self._plan_contacts()
        return res

    def _plan_contacts(self):
        """(Re)plan the 8 WHO contacts from the EDD; contacts already attended are kept."""
        Contact = self.env['medical.antenatal.contact']
        for preg in self.filtered('edd'):
            start = preg.edd - timedelta(days=280)
            existing = {c.week: c for c in preg.contact_ids}
            for week in ANC_WEEKS:
                due = start + timedelta(weeks=week)
                contact = existing.get(week)
                if contact:
                    if contact.state == 'planned':
                        contact.due_date = due
                else:
                    Contact.create({'pregnancy_id': preg.id, 'week': week, 'due_date': due})

    def action_book_next_contact(self):
        self.ensure_one()
        contact = self.next_contact_id
        if not contact:
            raise UserError(_("All antenatal contacts are done."))
        return contact.action_book()

    def action_delivered(self):
        for preg in self:
            if not preg.delivery_date or not preg.delivery_mode:
                raise UserError(_("Enter the delivery date and mode first."))
        self.write({'state': 'delivered'})
        self.contact_ids.filtered(lambda c: c.state == 'planned').write({'state': 'skipped'})

    def action_loss(self):
        self.write({'state': 'loss'})
        self.contact_ids.filtered(lambda c: c.state == 'planned').write({'state': 'skipped'})

    def action_register_baby(self):
        self.ensure_one()
        if not self.delivery_date:
            raise UserError(_("Enter the delivery date first."))
        if not self.baby_patient_id:
            self.baby_patient_id = self.env['res.partner'].create({
                'name': _("Baby of %s", self.patient_id.name),
                'is_patient': True,
                'birthdate': self.delivery_date,
                'parent_id': False,
                'phone': self.patient_id.phone,
                'emergency_contact': self.patient_id.name,
                'emergency_phone': self.patient_id.phone,
            })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'res_id': self.baby_patient_id.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }


class MedicalAntenatalContact(models.Model):
    _name = 'medical.antenatal.contact'
    _description = 'Antenatal Contact'
    _order = 'pregnancy_id, week'

    pregnancy_id = fields.Many2one('medical.pregnancy', required=True, ondelete='cascade', index=True)
    patient_id = fields.Many2one(related='pregnancy_id.patient_id', store=True)
    week = fields.Integer(string='Week', required=True)
    focus = fields.Char(compute='_compute_focus')
    due_date = fields.Date(required=True)
    visit_id = fields.Many2one('medical.visit', ondelete='set null')
    state = fields.Selection([('planned', 'Planned'), ('done', 'Done'), ('skipped', 'Missed / Not Needed')],
                             default='planned', required=True)
    is_overdue = fields.Boolean(compute='_compute_is_overdue')

    @api.depends('week')
    def _compute_focus(self):
        for contact in self:
            contact.focus = anc_focus(self.env, contact.week)

    @api.depends('week', 'due_date')
    def _compute_display_name(self):
        for contact in self:
            contact.display_name = _("Week %(week)s (%(date)s)", week=contact.week,
                                     date=contact.due_date and format_date(self.env, contact.due_date) or '')

    @api.depends('due_date', 'state')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for contact in self:
            contact.is_overdue = contact.state == 'planned' and contact.due_date < today - timedelta(days=7)

    def action_book(self):
        self.ensure_one()
        preg = self.pregnancy_id
        if not self.visit_id:
            consult = self.env.ref('medical_obgyn.product_antenatal_visit', raise_if_not_found=False)
            doctor = preg.doctor_id
            if not doctor:
                raise UserError(_("Choose the obstetrician of the pregnancy first."))
            self.visit_id = self.env['medical.visit'].create({
                'patient_id': preg.patient_id.id,
                'doctor_id': doctor.id,
                'pregnancy_id': preg.id,
                'visit_type': 'follow_up',
                'start': fields.Datetime.to_datetime(self.due_date) + timedelta(hours=9),
                'care_contract_id': preg.care_contract_id.id if preg.care_contract_id.state == 'active' else False,
                'line_ids': [(0, 0, {'product_id': consult.id,
                                     'care_contract_id': preg.care_contract_id.id if preg.care_contract_id.state == 'active' else False})]
                if consult else [],
            })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.visit',
            'res_id': self.visit_id.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }


class MedicalObstetricUs(models.Model):
    _name = 'medical.obstetric.us'
    _description = 'Obstetric Ultrasound'
    _order = 'date desc, id desc'

    pregnancy_id = fields.Many2one('medical.pregnancy', required=True, ondelete='cascade', index=True)
    patient_id = fields.Many2one(related='pregnancy_id.patient_id', store=True)
    date = fields.Date(default=fields.Date.context_today, required=True)
    ga_weeks = fields.Integer(string='GA by Scan (weeks)')
    ga_extra_days = fields.Integer(string='+ days')
    crl_mm = fields.Float(string='CRL (mm)', digits=(5, 1))
    bpd_mm = fields.Float(string='BPD (mm)', digits=(5, 1))
    hc_mm = fields.Float(string='HC (mm)', digits=(5, 1))
    ac_mm = fields.Float(string='AC (mm)', digits=(5, 1))
    fl_mm = fields.Float(string='FL (mm)', digits=(5, 1))
    efw_g = fields.Integer(string='Estimated Fetal Weight (g)')
    afi_cm = fields.Float(string='Amniotic Fluid Index (cm)', digits=(4, 1))
    fhr = fields.Integer(string='Fetal Heart Rate (bpm)')
    placenta = fields.Selection([('anterior', 'Anterior'), ('posterior', 'Posterior'), ('fundal', 'Fundal'),
                                 ('low', 'Low-lying'), ('previa', 'Praevia')])
    presentation = fields.Selection([('cephalic', 'Cephalic'), ('breech', 'Breech'), ('transverse', 'Transverse'),
                                     ('variable', 'Variable')])
    use_for_dating = fields.Boolean(string='Use to Date the Pregnancy')
    findings = fields.Text()
    flag = fields.Char(compute='_compute_flag')

    @api.depends('afi_cm', 'fhr', 'placenta')
    def _compute_flag(self):
        for us in self:
            flags = []
            if us.afi_cm and us.afi_cm < 5:
                flags.append(_("Low fluid (AFI < 5)"))
            if us.afi_cm and us.afi_cm > 24:
                flags.append(_("High fluid (AFI > 24)"))
            if us.fhr and not 110 <= us.fhr <= 160:
                flags.append(_("Fetal heart rate outside 110-160"))
            if us.placenta == 'previa':
                flags.append(_("Placenta praevia"))
            us.flag = ', '.join(flags) or False

    @api.model_create_multi
    def create(self, vals_list):
        scans = super().create(vals_list)
        scans._apply_dating()
        return scans

    def write(self, vals):
        res = super().write(vals)
        if {'use_for_dating', 'ga_weeks', 'ga_extra_days', 'date'} & set(vals):
            self._apply_dating()
        return res

    def _apply_dating(self):
        for us in self.filtered(lambda u: u.use_for_dating and u.ga_weeks):
            ga = us.ga_weeks * 7 + (us.ga_extra_days or 0)
            us.pregnancy_id.edd_us = us.date + timedelta(days=280 - ga)


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    pregnancy_id = fields.Many2one('medical.pregnancy', string='Pregnancy', index='btree_not_null',
                                   domain="[('patient_id', '=', patient_id), ('state', '=', 'active')]")
    ga_label = fields.Char(string='Gestational Age', compute='_compute_visit_ga')
    fundal_height = fields.Float(string='Fundal Height (cm)', digits=(4, 1), groups='medical_core.group_medical_nurse')
    fetal_heart_rate = fields.Integer(string='Fetal Heart Rate (bpm)', groups='medical_core.group_medical_nurse')
    presentation = fields.Selection([('cephalic', 'Cephalic'), ('breech', 'Breech'), ('transverse', 'Transverse'),
                                     ('unknown', 'Not assessed')], groups='medical_core.group_medical_nurse')
    fetal_movements = fields.Selection([('normal', 'Normal'), ('reduced', 'Reduced'), ('absent', 'Absent')],
                                       groups='medical_core.group_medical_nurse')
    edema = fields.Selection([('none', 'None'), ('mild', 'Mild'), ('marked', 'Marked')], groups='medical_core.group_medical_nurse')
    urine_protein = fields.Selection(DIPSTICK, groups='medical_core.group_medical_nurse')
    urine_glucose = fields.Selection(DIPSTICK, groups='medical_core.group_medical_nurse')
    obstetric_alert = fields.Char(compute='_compute_obstetric_alert', groups='medical_core.group_medical_nurse')

    @api.depends('pregnancy_id.edd', 'start')
    def _compute_visit_ga(self):
        for visit in self:
            edd = visit.pregnancy_id.edd
            visit.ga_label = ga_label(self.env, 280 - (edd - visit.start.date()).days) if edd and visit.start else False

    @api.depends('pregnancy_id', 'bp_systolic', 'bp_diastolic', 'urine_protein', 'fetal_heart_rate', 'fetal_movements',
                 'edema')
    def _compute_obstetric_alert(self):
        for visit in self:
            if not visit.pregnancy_id:
                visit.obstetric_alert = False
                continue
            alerts = []
            high_bp = (visit.bp_systolic or 0) >= 140 or (visit.bp_diastolic or 0) >= 90
            if high_bp:
                alerts.append(_("Blood pressure 140/90 or more"))
            if visit.urine_protein in ('2', '3') or (high_bp and visit.urine_protein in ('1', '2', '3')):
                alerts.append(_("Proteinuria: rule out pre-eclampsia"))
            if visit.fetal_heart_rate and not 110 <= visit.fetal_heart_rate <= 160:
                alerts.append(_("Fetal heart rate outside 110-160"))
            if visit.fetal_movements in ('reduced', 'absent'):
                alerts.append(_("Reduced fetal movements: assess fetal well-being today"))
            if visit.edema == 'marked':
                alerts.append(_("Marked oedema"))
            visit.obstetric_alert = ' · '.join(alerts) or False

    OB_OBS = {'fundal_height': 'medical_obgyn.obs_fundal_height', 'fetal_heart_rate': 'medical_obgyn.obs_fhr'}

    def _sync_obstetric_observations(self):
        Observation = self.env['medical.observation'].sudo()
        for visit in self.sudo():
            for field, xmlid in self.OB_OBS.items():
                obs_type = self.env.ref(xmlid, raise_if_not_found=False)
                current = Observation.search([('visit_id', '=', visit.id), ('type_id', '=', obs_type.id)])
                if visit[field]:
                    vals = {'value': visit[field], 'date': visit.start}
                    if current:
                        current.write(vals)
                    else:
                        Observation.create(dict(vals, patient_id=visit.patient_id.id, type_id=obs_type.id,
                                                visit_id=visit.id, source='exam'))
                elif current:
                    current.unlink()

    @api.model_create_multi
    def create(self, vals_list):
        visits = super().create(vals_list)
        if any(set(self.OB_OBS) & set(vals) for vals in vals_list):
            visits._sync_obstetric_observations()
        return visits

    def write(self, vals):
        res = super().write(vals)
        if set(self.OB_OBS) & set(vals):
            self._sync_obstetric_observations()
        return res

    def action_done(self):
        res = super().action_done()
        contacts = self.env['medical.antenatal.contact'].sudo().search([('visit_id', 'in', self.ids), ('state', '=', 'planned')])
        contacts.write({'state': 'done'})
        # a pregnancy visit not booked from a contact counts for the nearest planned contact
        for visit in self.filtered('pregnancy_id'):
            if visit.id in contacts.visit_id.ids:
                continue
            nearest = visit.pregnancy_id.contact_ids.filtered(lambda c: c.state == 'planned' and not c.visit_id).sorted(
                lambda c: abs((c.due_date - visit.start.date()).days))[:1]
            if nearest and abs((nearest.due_date - visit.start.date()).days) <= 21:
                nearest.sudo().write({'visit_id': visit.id, 'state': 'done'})
        return res

    def _brief_specialty_facts(self):
        facts = super()._brief_specialty_facts()
        preg = self.pregnancy_id
        if preg:
            rows = [_("%(ga)s, EDD %(edd)s, G%(g)s P%(p)s", ga=self.ga_label or '', edd=preg.edd, g=preg.gravida, p=preg.para)]
            if preg.risk_factor_ids:
                rows.append(_("Risk factors: %s", ', '.join(preg.risk_factor_ids.mapped('name'))))
            last_us = preg.ultrasound_ids[:1]
            if last_us:
                rows.append(_("Last scan %(date)s: EFW %(efw)s g, AFI %(afi)s", date=last_us.date, efw=last_us.efw_g or '-',
                              afi=last_us.afi_cm or '-'))
            facts.append((_("Pregnancy"), rows))
        return facts


class ResPartner(models.Model):
    _inherit = 'res.partner'

    pregnancy_ids = fields.One2many('medical.pregnancy', 'patient_id', string='Pregnancies')
    current_pregnancy_id = fields.Many2one('medical.pregnancy', compute='_compute_current_pregnancy')

    def _compute_current_pregnancy(self):
        for partner in self:
            partner.current_pregnancy_id = partner.pregnancy_ids.filtered(lambda p: p.state == 'active')[:1]


class MedicalCareContract(models.Model):
    _inherit = 'medical.care.contract'

    care_type = fields.Selection(selection_add=[('maternity', 'Maternity Package')],
                                 ondelete={'maternity': 'set default'})
