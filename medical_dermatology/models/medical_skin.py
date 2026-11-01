from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

FITZPATRICK = [
    ('1', 'I - always burns, never tans'),
    ('2', 'II - usually burns, tans minimally'),
    ('3', 'III - sometimes burns, tans gradually'),
    ('4', 'IV - burns minimally, tans well'),
    ('5', 'V - rarely burns, tans profusely'),
    ('6', 'VI - never burns'),
]
LESION_TYPES = [
    ('macule', 'Macule / patch'), ('papule', 'Papule'), ('nodule', 'Nodule'), ('plaque', 'Plaque'),
    ('vesicle', 'Vesicle / bulla'), ('pustule', 'Pustule'), ('ulcer', 'Ulcer'), ('nevus', 'Mole (naevus)'),
    ('wart', 'Wart'), ('scar', 'Scar'), ('other', 'Other'),
]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    fitzpatrick = fields.Selection(FITZPATRICK, string='Skin Type (Fitzpatrick)')
    skin_lesion_ids = fields.One2many('medical.skin.lesion', 'patient_id', string='Skin Lesions')
    skin_photo_ids = fields.One2many('medical.skin.photo', 'patient_id', string='Skin Photos')


class MedicalSkinLesion(models.Model):
    """A lesion followed over time with ABCDE screening and dated photos."""
    _name = 'medical.skin.lesion'
    _description = 'Skin Lesion'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'patient_id, name'

    name = fields.Char(required=True, copy=False, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade',
                                 domain=[('is_patient', '=', True)])
    body_site_id = fields.Many2one('medical.body.site', string='Site', required=True)
    laterality = fields.Selection([('right', 'Right'), ('left', 'Left')], string='Side')
    location_detail = fields.Char(string='Exact Location', help='e.g. 3 cm above the right scapula')
    lesion_type = fields.Selection(LESION_TYPES, required=True, default='nevus')
    size_mm = fields.Float(string='Size (mm)', digits=(5, 1), tracking=True)
    color = fields.Char()
    first_seen = fields.Date(default=fields.Date.context_today)
    diagnosis_id = fields.Many2one('medical.diagnosis', string='Diagnosis')
    # ABCDE screening for pigmented lesions
    abcde_asymmetry = fields.Boolean(string='A - Asymmetry')
    abcde_border = fields.Boolean(string='B - Irregular border')
    abcde_color = fields.Boolean(string='C - Several colours')
    abcde_diameter = fields.Boolean(string='D - Larger than 6 mm', compute='_compute_abcde_diameter', store=True, readonly=False)
    abcde_evolving = fields.Boolean(string='E - Changing', tracking=True)
    abcde_count = fields.Integer(compute='_compute_risk', store=True)
    risk = fields.Selection([('low', 'Low'), ('watch', 'Watch'), ('high', 'Suspicious - consider biopsy')],
                            compute='_compute_risk', store=True, tracking=True)
    status = fields.Selection([('monitoring', 'Monitoring'), ('treated', 'Treated'), ('biopsied', 'Biopsied'),
                               ('excised', 'Excised'), ('resolved', 'Resolved')], default='monitoring', required=True,
                              tracking=True)
    check_every_months = fields.Integer(string='Check Every (months)', default=6)
    last_check = fields.Date(tracking=True)
    next_check = fields.Date(compute='_compute_next_check', store=True)
    photo_ids = fields.One2many('medical.skin.photo', 'lesion_id', string='Photos')
    photo_count = fields.Integer(compute='_compute_photo_count')
    note = fields.Text()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('size_mm')
    def _compute_abcde_diameter(self):
        for lesion in self:
            lesion.abcde_diameter = lesion.size_mm > 6

    @api.depends('abcde_asymmetry', 'abcde_border', 'abcde_color', 'abcde_diameter', 'abcde_evolving')
    def _compute_risk(self):
        for lesion in self:
            count = sum(bool(lesion[f]) for f in ('abcde_asymmetry', 'abcde_border', 'abcde_color', 'abcde_diameter',
                                                  'abcde_evolving'))
            lesion.abcde_count = count
            if count >= 3 or (lesion.abcde_evolving and count >= 2):
                lesion.risk = 'high'
            elif count:
                lesion.risk = 'watch'
            else:
                lesion.risk = 'low'

    @api.depends('last_check', 'first_seen', 'check_every_months', 'status')
    def _compute_next_check(self):
        for lesion in self:
            base = lesion.last_check or lesion.first_seen
            lesion.next_check = base + relativedelta(months=lesion.check_every_months) \
                if base and lesion.status == 'monitoring' and lesion.check_every_months else False

    def _compute_photo_count(self):
        for lesion in self:
            lesion.photo_count = len(lesion.photo_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New') and vals.get('patient_id'):
                count = self.search_count([('patient_id', '=', vals['patient_id'])])
                vals['name'] = _("Lesion %s", count + 1)
        lesions = super().create(vals_list)
        lesions._schedule_check()
        return lesions

    def write(self, vals):
        res = super().write(vals)
        if {'last_check', 'check_every_months', 'status'} & set(vals):
            self.activity_ids.unlink()
            self._schedule_check()
        return res

    def _schedule_check(self):
        for lesion in self.filtered(lambda l: l.next_check and l.status == 'monitoring'):
            lesion.activity_schedule('mail.mail_activity_data_call', date_deadline=lesion.next_check,
                                     summary=_("Skin check: %(lesion)s - %(patient)s", lesion=lesion.display_name,
                                               patient=lesion.patient_id.name))

    @api.depends('name', 'body_site_id', 'lesion_type')
    def _compute_display_name(self):
        types = dict(self._fields['lesion_type']._description_selection(self.env))
        for lesion in self:
            lesion.display_name = f"{lesion.name} - {types.get(lesion.lesion_type, '')} {lesion.body_site_id.name or ''}".strip()

    def action_checked_today(self):
        self.write({'last_check': fields.Date.context_today(self)})

    def action_view_photos(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Photos: %s", self.display_name),
            'res_model': 'medical.skin.photo',
            'view_mode': 'kanban,list,form',
            'domain': [('lesion_id', '=', self.id)],
            'context': {'default_lesion_id': self.id, 'default_patient_id': self.patient_id.id,
                        'default_body_site_id': self.body_site_id.id},
        }


class MedicalSkinPhoto(models.Model):
    _name = 'medical.skin.photo'
    _description = 'Clinical Photo'
    _order = 'date desc, id desc'

    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade',
                                 domain=[('is_patient', '=', True)])
    lesion_id = fields.Many2one('medical.skin.lesion', index='btree_not_null', ondelete='cascade',
                                domain="[('patient_id', '=', patient_id)]")
    care_contract_id = fields.Many2one('medical.care.contract', string='Program', index='btree_not_null',
                                       domain="[('patient_id', '=', patient_id)]")
    visit_id = fields.Many2one('medical.visit', ondelete='set null')
    body_site_id = fields.Many2one('medical.body.site', string='Area')
    date = fields.Date(default=fields.Date.context_today, required=True)
    stage = fields.Selection([('before', 'Before'), ('during', 'During Treatment'), ('after', 'After'),
                              ('follow_up', 'Follow-up')], default='follow_up', required=True)
    image = fields.Image(max_width=1920, max_height=1920, required=True)
    image_small = fields.Image(string='Thumbnail', related='image', max_width=512, max_height=512, store=True)
    consent_to_use = fields.Boolean(string='Patient agreed to the photo', default=True)
    note = fields.Char()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    is_derm = fields.Boolean(compute='_compute_is_derm')
    patient_fitzpatrick = fields.Selection(related='patient_id.fitzpatrick', readonly=False)
    laser_device = fields.Char(string='Device')
    laser_area = fields.Char(string='Area Treated')
    laser_fluence = fields.Float(string='Fluence (J/cm²)', digits=(5, 1))
    laser_spot_mm = fields.Float(string='Spot Size (mm)', digits=(4, 1))
    laser_pulse_ms = fields.Float(string='Pulse Width (ms)', digits=(5, 1))
    laser_passes = fields.Integer(string='Passes')
    skin_reaction = fields.Selection([('none', 'None'), ('erythema', 'Mild redness'), ('edema', 'Swelling'),
                                      ('blister', 'Blistering'), ('pigment', 'Pigment change')], string='Skin Reaction')
    skin_photo_ids = fields.One2many('medical.skin.photo', 'visit_id', string='Photos')

    @api.depends('department_id', 'care_contract_id.care_type')
    def _compute_is_derm(self):
        derm = self.env.ref('medical_dermatology.dept_dermatology', raise_if_not_found=False)
        for visit in self:
            visit.is_derm = (bool(derm) and visit.department_id == derm) or visit.care_contract_id.care_type == 'aesthetic'

    def _previous_laser_settings(self):
        """Settings of the last session of the same program: the starting point for this one."""
        self.ensure_one()
        if not self.care_contract_id:
            return self.browse()
        return self.search([('care_contract_id', '=', self.care_contract_id.id), ('id', '!=', self.id),
                            ('state', 'in', ('done', 'invoiced')), ('laser_fluence', '>', 0)], order='start desc', limit=1)

    def action_copy_last_settings(self):
        for visit in self:
            last = visit._previous_laser_settings()
            if last:
                visit.write({f: last[f] for f in ('laser_device', 'laser_area', 'laser_fluence', 'laser_spot_mm',
                                                  'laser_pulse_ms', 'laser_passes')})
        return True

    def _brief_specialty_facts(self):
        facts = super()._brief_specialty_facts()
        if self.is_derm:
            lesions = self.patient_id.skin_lesion_ids.filtered(lambda l: l.status == 'monitoring')
            rows = [f"{l.display_name}: {dict(l._fields['risk']._description_selection(self.env)).get(l.risk)}"
                    for l in lesions]
            last = self._previous_laser_settings()
            if last:
                rows.append(_("Last laser session: %(f)s J/cm², spot %(s)s mm, reaction %(r)s", f=last.laser_fluence,
                              s=last.laser_spot_mm, r=last.skin_reaction or '-'))
            facts.append((_("Skin"), rows))
        return facts


class MedicalCareContract(models.Model):
    _inherit = 'medical.care.contract'

    care_type = fields.Selection(selection_add=[('aesthetic', 'Aesthetic / Laser Program')],
                                 ondelete={'aesthetic': 'set default'})
    photo_ids = fields.One2many('medical.skin.photo', 'care_contract_id', string='Before / After Photos')
