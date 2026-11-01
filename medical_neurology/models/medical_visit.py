from odoo import api, fields, models

POWER = [(str(i), str(i)) for i in range(6)]
REFLEX = [('0', '0 (absent)'), ('1', '1+ (reduced)'), ('2', '2+ (normal)'), ('3', '3+ (brisk)'), ('4', '4+ (clonus)')]
PLANTAR = [('flexor', 'Flexor (down)'), ('extensor', 'Extensor (Babinski)'), ('equivocal', 'Equivocal')]
DOCTOR = 'medical_core.group_medical_doctor'


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    is_neuro = fields.Boolean(compute='_compute_is_neuro')
    neuro_consciousness = fields.Selection([('alert', 'Alert'), ('drowsy', 'Drowsy'), ('stupor', 'Stuporous'),
                                            ('coma', 'Comatose')], string='Consciousness', groups=DOCTOR)
    neuro_orientation = fields.Selection([('full', 'Oriented to time, place and person'), ('partial', 'Partly oriented'),
                                          ('none', 'Disoriented')], string='Orientation', groups=DOCTOR)
    neuro_speech = fields.Selection([('normal', 'Normal'), ('dysarthria', 'Dysarthria'), ('expressive', 'Expressive aphasia'),
                                     ('receptive', 'Receptive aphasia'), ('global', 'Global aphasia')],
                                    string='Speech', groups=DOCTOR)
    neuro_cranial_nerves = fields.Selection([('normal', 'Normal'), ('abnormal', 'Abnormal')], string='Cranial Nerves',
                                            groups=DOCTOR)
    neuro_cranial_note = fields.Char(string='Cranial Nerves Findings', groups=DOCTOR)
    neuro_power_rul = fields.Selection(POWER, string='Power Right Arm', groups=DOCTOR)
    neuro_power_lul = fields.Selection(POWER, string='Power Left Arm', groups=DOCTOR)
    neuro_power_rll = fields.Selection(POWER, string='Power Right Leg', groups=DOCTOR)
    neuro_power_lll = fields.Selection(POWER, string='Power Left Leg', groups=DOCTOR)
    neuro_tone = fields.Selection([('normal', 'Normal'), ('spastic', 'Increased (spastic)'), ('rigid', 'Increased (rigid)'),
                                   ('reduced', 'Reduced')], string='Tone', groups=DOCTOR)
    neuro_reflex_ru = fields.Selection(REFLEX, string='Reflexes Right Arm', groups=DOCTOR)
    neuro_reflex_lu = fields.Selection(REFLEX, string='Reflexes Left Arm', groups=DOCTOR)
    neuro_reflex_rl = fields.Selection(REFLEX, string='Reflexes Right Leg', groups=DOCTOR)
    neuro_reflex_ll = fields.Selection(REFLEX, string='Reflexes Left Leg', groups=DOCTOR)
    neuro_plantar_r = fields.Selection(PLANTAR, string='Plantar Right', groups=DOCTOR)
    neuro_plantar_l = fields.Selection(PLANTAR, string='Plantar Left', groups=DOCTOR)
    neuro_sensation = fields.Selection([('normal', 'Normal'), ('reduced', 'Reduced'), ('absent', 'Absent')],
                                       string='Sensation', groups=DOCTOR)
    neuro_sensation_note = fields.Char(string='Sensory Pattern', groups=DOCTOR,
                                       help='e.g. glove and stocking, L5 dermatome, left hemibody')
    neuro_coordination = fields.Selection([('normal', 'Normal'), ('ataxic', 'Ataxic')], string='Coordination', groups=DOCTOR)
    neuro_gait = fields.Selection([('normal', 'Normal'), ('antalgic', 'Antalgic'), ('ataxic', 'Ataxic'),
                                   ('hemiparetic', 'Hemiparetic'), ('parkinsonian', 'Parkinsonian'),
                                   ('steppage', 'Steppage'), ('unable', 'Unable to walk')], string='Gait', groups=DOCTOR)
    neuro_meningism = fields.Boolean(string='Meningism', groups=DOCTOR)
    neuro_focal_deficit = fields.Boolean(compute='_compute_neuro_focal', store=True, groups=DOCTOR,
                                         string='Focal Deficit',
                                         help='Left / right power difference, extensor plantar or aphasia.')
    neuro_findings = fields.Char(compute='_compute_neuro_focal', store=True, groups=DOCTOR, string='Focal Findings')

    @api.depends('department_id')
    def _compute_is_neuro(self):
        neuro = self.env.ref('medical_neurology.dept_neurology', raise_if_not_found=False)
        for visit in self:
            visit.is_neuro = bool(neuro) and visit.department_id == neuro

    @api.depends('neuro_power_rul', 'neuro_power_lul', 'neuro_power_rll', 'neuro_power_lll',
                 'neuro_plantar_r', 'neuro_plantar_l', 'neuro_speech')
    def _compute_neuro_focal(self):
        for visit in self:
            findings = []
            pairs = (('neuro_power_rul', 'neuro_power_lul', 'arm'), ('neuro_power_rll', 'neuro_power_lll', 'leg'))
            for right, left, limb in pairs:
                r, l = visit[right], visit[left]
                if r and l and r != l:
                    weak = 'right' if int(r) < int(l) else 'left'
                    findings.append(f"{weak} {limb} weaker ({min(r, l)}/5)")
            for side, field in (('right', 'neuro_plantar_r'), ('left', 'neuro_plantar_l')):
                if visit[field] == 'extensor':
                    findings.append(f"{side} Babinski")
            if visit.neuro_speech in ('expressive', 'receptive', 'global'):
                findings.append('aphasia')
            visit.neuro_focal_deficit = bool(findings)
            visit.neuro_findings = ', '.join(findings) or False

    def _brief_specialty_facts(self):
        facts = super()._brief_specialty_facts()
        if self.is_neuro:
            events = self.env['medical.neuro.event'].sudo().search(
                [('patient_id', '=', self.patient_id.id)], order='date desc', limit=5)
            rows = [f"{e.date.date()}: {e.display_name}" for e in events]
            if self.patient_id.sudo().last_seizure_date:
                rows.insert(0, f"seizure-free for {self.patient_id.sudo().seizure_free_days} days")
            facts.append(('Seizure / headache diary', rows))
        return facts
