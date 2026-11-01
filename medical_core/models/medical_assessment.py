from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class MedicalAssessmentTemplate(models.Model):
    _name = 'medical.assessment.template'
    _description = 'Clinical Score / Questionnaire'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    department_ids = fields.Many2many('medical.department', string='Departments')
    description = fields.Html(translate=True, help='How to use the score, shown above the questions.')
    item_ids = fields.One2many('medical.assessment.item', 'template_id', string='Questions', copy=True)
    range_ids = fields.One2many('medical.assessment.range', 'template_id', string='Interpretation', copy=True)
    score_mode = fields.Selection([
        ('sum', 'Sum of answers'),
        ('percent', 'Percentage of the maximum (answered items)'),
    ], default='sum', required=True)
    observation_type_id = fields.Many2one('medical.observation.type', string='Store Score as',
                                          help='Each completed score is also a measurement, so it can be trended.')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code)', 'The score code must be unique.')

    def _interpret(self, score):
        self.ensure_one()
        for rng in self.range_ids.sorted('min_score'):
            if rng.min_score <= score <= rng.max_score:
                return rng
        return self.range_ids.browse()


class MedicalAssessmentItem(models.Model):
    _name = 'medical.assessment.item'
    _description = 'Score Question'
    _order = 'template_id, sequence, id'

    template_id = fields.Many2one('medical.assessment.template', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Question', required=True, translate=True)
    section = fields.Char(translate=True)
    answer_type = fields.Selection([('choice', 'Choice'), ('number', 'Number')], default='choice', required=True)
    min_value = fields.Float()
    max_value = fields.Float()
    option_ids = fields.One2many('medical.assessment.option', 'item_id', string='Answers', copy=True)
    max_score = fields.Float(compute='_compute_max_score')

    @api.depends('option_ids.score', 'answer_type', 'max_value')
    def _compute_max_score(self):
        for item in self:
            item.max_score = item.max_value if item.answer_type == 'number' else max(item.option_ids.mapped('score') or [0])


class MedicalAssessmentOption(models.Model):
    _name = 'medical.assessment.option'
    _description = 'Score Answer'
    _order = 'item_id, sequence, id'

    item_id = fields.Many2one('medical.assessment.item', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    score = fields.Float(required=True)
    is_alert = fields.Boolean(string='Safety Alert', help='Choosing this answer shows an alert to act on now '
                                                           '(e.g. thoughts of self-harm).')

    @api.depends('name', 'score')
    def _compute_display_name(self):
        for option in self:
            option.display_name = f"{option.score:g} · {option.name}"


class MedicalAssessmentRange(models.Model):
    _name = 'medical.assessment.range'
    _description = 'Score Interpretation'
    _order = 'template_id, min_score'

    template_id = fields.Many2one('medical.assessment.template', required=True, ondelete='cascade', index=True)
    min_score = fields.Float(required=True)
    max_score = fields.Float(required=True)
    name = fields.Char(string='Meaning', required=True, translate=True)
    severity = fields.Selection([('none', 'None'), ('mild', 'Mild'), ('moderate', 'Moderate'), ('severe', 'Severe')],
                                default='none', required=True)


class MedicalAssessment(models.Model):
    _name = 'medical.assessment'
    _description = 'Completed Clinical Score'
    _order = 'date desc, id desc'

    template_id = fields.Many2one('medical.assessment.template', string='Questionnaire', required=True, index=True)
    patient_id = fields.Many2one('res.partner', required=True, index=True, ondelete='cascade',
                                 domain=[('is_patient', '=', True)])
    visit_id = fields.Many2one('medical.visit', index='btree_not_null', ondelete='set null')
    department_id = fields.Many2one('medical.department')
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, string='Recorded by')
    answer_ids = fields.One2many('medical.assessment.answer', 'assessment_id', string='Answers')
    score = fields.Float(compute='_compute_score', store=True, digits=(16, 1))
    max_score = fields.Float(compute='_compute_score', store=True, digits=(16, 1))
    complete = fields.Boolean(compute='_compute_score', store=True)
    interpretation = fields.Char(compute='_compute_score', store=True)
    severity = fields.Selection(related='range_id.severity', store=True)
    range_id = fields.Many2one('medical.assessment.range', compute='_compute_score', store=True)
    observation_id = fields.Many2one('medical.observation', readonly=True, copy=False)
    has_alert = fields.Boolean(compute='_compute_alert', store=True)
    alert_text = fields.Char(compute='_compute_alert', store=True)
    note = fields.Text()

    @api.depends('answer_ids.option_id.is_alert')
    def _compute_alert(self):
        for assessment in self:
            alerts = assessment.answer_ids.filtered(lambda a: a.option_id.is_alert)
            assessment.has_alert = bool(alerts)
            assessment.alert_text = '; '.join(f"{a.item_id.name}: {a.option_id.name}" for a in alerts) or False

    @api.depends('answer_ids.score', 'answer_ids.answered', 'template_id.score_mode')
    def _compute_score(self):
        for assessment in self:
            answered = assessment.answer_ids.filtered('answered')
            total = sum(answered.mapped('score'))
            maximum = sum(answered.item_id.mapped('max_score'))
            if assessment.template_id.score_mode == 'percent':
                total = 100.0 * total / maximum if maximum else 0.0
                maximum = 100.0
            else:
                maximum = sum(assessment.answer_ids.item_id.mapped('max_score'))
            assessment.score = total
            assessment.max_score = maximum
            assessment.complete = bool(assessment.answer_ids) and len(answered) == len(assessment.answer_ids)
            rng = assessment.template_id._interpret(total) if assessment.template_id else False
            assessment.range_id = rng
            assessment.interpretation = rng.name if rng else False

    @api.onchange('template_id')
    def _onchange_template_id(self):
        self.answer_ids = [(5, 0, 0)] + [(0, 0, {'item_id': item.id}) for item in self.template_id.item_ids]

    @api.model_create_multi
    def create(self, vals_list):
        assessments = super().create(vals_list)
        for assessment in assessments.filtered(lambda a: not a.answer_ids):
            assessment.answer_ids = [(0, 0, {'item_id': item.id}) for item in assessment.template_id.item_ids]
        return assessments

    @api.depends('template_id', 'score', 'max_score')
    def _compute_display_name(self):
        for assessment in self:
            assessment.display_name = f"{assessment.template_id.name or ''}: {assessment.score:g}/{assessment.max_score:g}"

    def action_save(self):
        """Store the score as a measurement once every question is answered."""
        for assessment in self:
            if not assessment.complete:
                raise UserError(_("Answer every question of %s first.", assessment.template_id.name))
            obs_type = assessment.template_id.observation_type_id
            if not obs_type:
                continue
            vals = {'value': assessment.score, 'date': assessment.date}
            if assessment.observation_id:
                assessment.observation_id.sudo().write(vals)
            else:
                assessment.observation_id = self.env['medical.observation'].sudo().create(dict(
                    vals, patient_id=assessment.patient_id.id, type_id=obs_type.id,
                    visit_id=assessment.visit_id.id, source='assessment'))
        return {'type': 'ir.actions.act_window_close'}


class MedicalAssessmentAnswer(models.Model):
    _name = 'medical.assessment.answer'
    _description = 'Score Answer Given'
    _order = 'sequence, id'

    assessment_id = fields.Many2one('medical.assessment', required=True, ondelete='cascade', index=True)
    item_id = fields.Many2one('medical.assessment.item', required=True, ondelete='cascade')
    sequence = fields.Integer(related='item_id.sequence', store=True)
    section = fields.Char(related='item_id.section')
    answer_type = fields.Selection(related='item_id.answer_type')
    option_id = fields.Many2one('medical.assessment.option', string='Answer', domain="[('item_id', '=', item_id)]")
    number = fields.Float(string='Value')
    answered = fields.Boolean(compute='_compute_score', store=True)
    score = fields.Float(compute='_compute_score', store=True)

    @api.depends('option_id', 'number', 'answer_type')
    def _compute_score(self):
        for answer in self:
            if answer.answer_type == 'number':
                answer.answered = True
                answer.score = answer.number
            else:
                answer.answered = bool(answer.option_id)
                answer.score = answer.option_id.score if answer.option_id else 0.0

    @api.constrains('number')
    def _check_number(self):
        for answer in self.filtered(lambda a: a.answer_type == 'number'):
            item = answer.item_id
            if (item.min_value or item.max_value) and not item.min_value <= answer.number <= item.max_value:
                raise ValidationError(_("%(q)s: the value must be between %(a)s and %(b)s.",
                                        q=item.name, a=item.min_value, b=item.max_value))
