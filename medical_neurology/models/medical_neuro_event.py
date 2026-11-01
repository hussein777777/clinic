from odoo import api, fields, models, _

SEIZURE_TYPES = [
    ('focal_aware', 'Focal aware'),
    ('focal_impaired', 'Focal with impaired awareness'),
    ('focal_to_bilateral', 'Focal to bilateral tonic-clonic'),
    ('gtc', 'Generalized tonic-clonic'),
    ('absence', 'Absence'),
    ('myoclonic', 'Myoclonic'),
    ('atonic', 'Atonic'),
    ('unknown', 'Unknown onset'),
]


class MedicalNeuroEvent(models.Model):
    """Patient diary entry: a seizure or a headache, as reported by the patient, a witness or the clinic."""
    _name = 'medical.neuro.event'
    _description = 'Seizure / Headache Diary Entry'
    _order = 'date desc, id desc'

    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade',
                                 domain=[('is_patient', '=', True)])
    date = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    event_type = fields.Selection([('seizure', 'Seizure'), ('headache', 'Headache'), ('other', 'Other Episode')],
                                  default='seizure', required=True)
    seizure_type = fields.Selection(SEIZURE_TYPES)
    duration_min = fields.Float(string='Duration (min)')
    severity = fields.Integer(string='Severity (0-10)')
    aura = fields.Boolean()
    triggers = fields.Char(help='Missed medicine, lack of sleep, fever, stress, menstruation, food...')
    rescue_medication = fields.Boolean(string='Rescue Medicine Used')
    emergency_visit = fields.Boolean(string='Went to Emergency')
    reported_by = fields.Selection([('patient', 'Patient'), ('witness', 'Witness / Family'), ('clinic', 'Seen in Clinic')],
                                   default='patient')
    note = fields.Char()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('event_type', 'seizure_type', 'severity', 'duration_min')
    def _compute_display_name(self):
        types = dict(self._fields['event_type']._description_selection(self.env))
        seizures = dict(self._fields['seizure_type']._description_selection(self.env))
        for event in self:
            label = seizures.get(event.seizure_type) if event.event_type == 'seizure' and event.seizure_type \
                else types.get(event.event_type, '')
            extra = []
            if event.duration_min:
                extra.append(_("%s min", f"{event.duration_min:g}"))
            if event.event_type == 'headache' and event.severity:
                extra.append(f"{event.severity}/10")
            event.display_name = label + (f" ({', '.join(extra)})" if extra else '')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    neuro_event_ids = fields.One2many('medical.neuro.event', 'patient_id', string='Seizure / Headache Diary',
                                      groups='medical_core.group_medical_nurse')
    last_seizure_date = fields.Datetime(compute='_compute_seizure_stats', groups='medical_core.group_medical_nurse')
    seizure_free_days = fields.Integer(compute='_compute_seizure_stats', groups='medical_core.group_medical_nurse')
    seizures_90d = fields.Integer(string='Seizures (90 days)', compute='_compute_seizure_stats',
                                  groups='medical_core.group_medical_nurse')
    headache_days_30d = fields.Integer(string='Headache Days (30 days)', compute='_compute_seizure_stats',
                                       groups='medical_core.group_medical_nurse')

    def _compute_seizure_stats(self):
        now = fields.Datetime.now()
        Event = self.env['medical.neuro.event'].sudo()
        for partner in self:
            seizures = Event.search([('patient_id', '=', partner.id), ('event_type', '=', 'seizure')], order='date desc')
            last = seizures[:1].date
            partner.last_seizure_date = last
            partner.seizure_free_days = (now - last).days if last else 0
            partner.seizures_90d = len(seizures.filtered(lambda e: (now - e.date).days < 90))
            headaches = Event.search([('patient_id', '=', partner.id), ('event_type', '=', 'headache'),
                                      ('date', '>', fields.Datetime.subtract(now, days=30))])
            partner.headache_days_30d = len({h.date.date() for h in headaches})
