from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

FLAGS = [
    ('normal', 'Normal'),
    ('low', 'Low'),
    ('high', 'High'),
    ('critical_low', 'Critically Low'),
    ('critical_high', 'Critically High'),
]


class MedicalObservationType(models.Model):
    _name = 'medical.observation.type'
    _description = 'Measurement Type'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    unit = fields.Char(translate=True)
    sequence = fields.Integer(default=10)
    decimals = fields.Integer(default=1)
    category = fields.Selection([
        ('vital', 'Vital Sign'),
        ('lab', 'Lab Result'),
        ('exam', 'Examination'),
        ('score', 'Clinical Score'),
        ('growth', 'Growth'),
    ], default='lab', required=True)
    department_ids = fields.Many2many('medical.department', string='Departments')
    normal_min = fields.Float(string='Normal from')
    normal_max = fields.Float(string='Normal to')
    critical_min = fields.Float(string='Critical below')
    critical_max = fields.Float(string='Critical above')
    lower_is_better = fields.Boolean(help='Used for the arrow colour on trends (e.g. HbA1c, pain).')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code)', 'The measurement code must be unique.')

    def _flag(self, value):
        """Classify a value against this type's limits (0 limits are ignored)."""
        self.ensure_one()
        if value is None or value is False:
            return False
        if self.critical_min and value < self.critical_min:
            return 'critical_low'
        if self.critical_max and value > self.critical_max:
            return 'critical_high'
        if self.normal_min and value < self.normal_min:
            return 'low'
        if self.normal_max and value > self.normal_max:
            return 'high'
        return 'normal'

    @api.depends('name', 'unit')
    def _compute_display_name(self):
        for obs_type in self:
            obs_type.display_name = f"{obs_type.name} ({obs_type.unit})" if obs_type.unit else obs_type.name


class MedicalObservation(models.Model):
    _name = 'medical.observation'
    _description = 'Measurement'
    _order = 'date desc, id desc'

    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade',
                                 domain=[('is_patient', '=', True)])
    type_id = fields.Many2one('medical.observation.type', string='Measurement', required=True, index=True,
                              ondelete='restrict')
    category = fields.Selection(related='type_id.category', store=True)
    unit = fields.Char(related='type_id.unit')
    value = fields.Float(required=True, digits=(16, 3))
    laterality = fields.Selection([('right', 'Right'), ('left', 'Left')], string='Side',
                                  help='For paired organs and joints: eye pressure, knee flexion...')
    date = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    visit_id = fields.Many2one('medical.visit', index='btree_not_null', ondelete='set null')
    source = fields.Selection([
        ('manual', 'Manual'),
        ('vitals', 'Visit Vitals'),
        ('order', 'Lab / Imaging Result'),
        ('assessment', 'Clinical Score'),
        ('exam', 'Examination'),
        ('device', 'Device'),
    ], default='manual', required=True)
    flag = fields.Selection(FLAGS, compute='_compute_flag', store=True)
    note = fields.Char()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('value', 'type_id.normal_min', 'type_id.normal_max', 'type_id.critical_min', 'type_id.critical_max')
    def _compute_flag(self):
        for obs in self:
            obs.flag = obs.type_id._flag(obs.value) if obs.type_id else False

    @api.depends('type_id', 'value', 'laterality')
    def _compute_display_name(self):
        sides = dict(self._fields['laterality']._description_selection(self.env))
        for obs in self:
            decimals = obs.type_id.decimals if obs.type_id else 1
            name = obs.type_id.name or ''
            if obs.laterality:
                name = f"{name} ({sides[obs.laterality]})"
            obs.display_name = f"{name}: {obs.value:.{max(decimals, 0)}f} {obs.unit or ''}".strip()

    @api.constrains('value', 'type_id')
    def _check_value(self):
        for obs in self:
            if obs.type_id.category == 'vital' and obs.value < 0:
                raise ValidationError(_("A vital sign cannot be negative."))

    @api.model
    def get_trend(self, patient_id, type_ids=None, limit=60):
        """Series for the trend widget: per measurement type, the last `limit` values (oldest first)."""
        patient = self.env['res.partner'].browse(patient_id)
        patient.check_access('read')
        domain = [('patient_id', '=', patient_id)]
        if type_ids:
            domain.append(('type_id', 'in', type_ids))
        sides = dict(self._fields['laterality']._description_selection(self.env))
        groups = self._read_group(domain, ['type_id', 'laterality'], ['__count'], order='type_id')
        series = []
        for obs_type, side, count in groups:
            records = self.search(domain + [('type_id', '=', obs_type.id), ('laterality', '=', side)],
                                  order='date desc', limit=limit)
            records = records.sorted('date')
            series.append({
                'type_id': f"{obs_type.id}-{side or ''}",
                'code': obs_type.code,
                'side': side or False,
                'name': f"{obs_type.name} ({sides[side]})" if side else obs_type.name,
                'unit': obs_type.unit or '',
                'decimals': obs_type.decimals,
                'category': obs_type.category,
                'count': count,
                'normal_min': obs_type.normal_min or None,
                'normal_max': obs_type.normal_max or None,
                'lower_is_better': obs_type.lower_is_better,
                'points': [{
                    'date': fields.Datetime.to_string(r.date),
                    'label': fields.Date.to_string(fields.Datetime.context_timestamp(r, r.date).date()),
                    'value': r.value,
                    'flag': r.flag,
                } for r in records],
            })
        return series
