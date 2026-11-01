from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _


class MedicalChronicProgram(models.Model):
    """Template of a chronic disease follow-up program: targets, tests and review interval."""
    _name = 'medical.chronic.program'
    _description = 'Chronic Disease Program'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    diagnosis_ids = fields.Many2many('medical.diagnosis', string='Diagnoses',
                                     help='Patients with these diagnoses are offered the program.')
    target_ids = fields.One2many('medical.chronic.target', 'program_id', string='Targets', copy=True)
    test_ids = fields.One2many('medical.chronic.test', 'program_id', string='Periodic Tests', copy=True)
    review_months = fields.Integer(string='Review Every (months)', default=3, required=True)
    advice = fields.Html(translate=True, help='Printed for the patient: diet, activity, warning signs.')
    active = fields.Boolean(default=True)


class MedicalChronicTarget(models.Model):
    _name = 'medical.chronic.target'
    _description = 'Program Target'
    _order = 'program_id, sequence, id'

    program_id = fields.Many2one('medical.chronic.program', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    type_id = fields.Many2one('medical.observation.type', string='Measurement', required=True)
    target_min = fields.Float(string='At least')
    target_max = fields.Float(string='At most')

    @api.depends('type_id', 'target_min', 'target_max')
    def _compute_display_name(self):
        for target in self:
            if target.target_min and target.target_max:
                rng = f"{target.target_min:g}–{target.target_max:g}"
            elif target.target_max:
                rng = f"≤ {target.target_max:g}"
            else:
                rng = f"≥ {target.target_min:g}"
            target.display_name = f"{target.type_id.name} {rng} {target.type_id.unit or ''}".strip()

    def _met(self, value):
        self.ensure_one()
        if self.target_min and value < self.target_min:
            return False
        if self.target_max and value > self.target_max:
            return False
        return True


class MedicalChronicTest(models.Model):
    _name = 'medical.chronic.test'
    _description = 'Program Periodic Test'
    _order = 'program_id, sequence, id'

    program_id = fields.Many2one('medical.chronic.program', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', string='Test', required=True,
                                 domain=[('procedure_type', 'in', ('lab', 'imaging', 'test', 'consultation', 'procedure'))])
    interval_months = fields.Integer(string='Every (months)', default=12, required=True)


class MedicalChronicEnrollment(models.Model):
    """A patient followed in a chronic program: targets, control status, due tests and next review."""
    _name = 'medical.chronic.enrollment'
    _description = 'Chronic Program Enrollment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'next_review_date, id'

    patient_id = fields.Many2one('res.partner', required=True, index=True, tracking=True,
                                 domain=[('is_patient', '=', True)])
    program_id = fields.Many2one('medical.chronic.program', required=True, tracking=True)
    doctor_id = fields.Many2one('hr.employee', string='Doctor', domain=[('is_practitioner', '=', True)], tracking=True)
    start_date = fields.Date(default=fields.Date.context_today, required=True)
    last_review_date = fields.Date(tracking=True)
    next_review_date = fields.Date(compute='_compute_next_review', store=True, tracking=True)
    state = fields.Selection([('active', 'Active'), ('paused', 'Paused'), ('stopped', 'Stopped')],
                             default='active', required=True, tracking=True)
    control_status = fields.Selection([
        ('unknown', 'No recent results'),
        ('controlled', 'On target'),
        ('partial', 'Partly on target'),
        ('uncontrolled', 'Off target'),
    ], default='unknown', readonly=True, tracking=True, index=True)
    control_detail = fields.Text(readonly=True)
    tests_due = fields.Char(compute='_compute_tests_due')
    has_tests_due = fields.Boolean(compute='_compute_tests_due', search='_search_has_tests_due')
    is_review_due = fields.Boolean(compute='_compute_next_review', store=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    _patient_program_uniq = models.Constraint('UNIQUE(patient_id, program_id)', 'The patient is already in this program.')

    @api.depends('program_id.review_months', 'last_review_date', 'start_date')
    def _compute_next_review(self):
        today = fields.Date.context_today(self)
        for enrollment in self:
            base = enrollment.last_review_date or enrollment.start_date
            months = enrollment.program_id.review_months or 3
            enrollment.next_review_date = base + relativedelta(months=months) if base else False
            enrollment.is_review_due = bool(enrollment.next_review_date and enrollment.next_review_date <= today)

    @api.depends('patient_id', 'program_id')
    def _compute_display_name(self):
        for enrollment in self:
            enrollment.display_name = f"{enrollment.program_id.name or ''} - {enrollment.patient_id.name or ''}"

    # ------------------------------------------------------------- due tests
    def _last_done(self, product):
        """Date the test was last performed for the patient (executed line, or a result for its components)."""
        self.ensure_one()
        line = self.env['medical.treatment.line'].sudo().search([
            ('patient_id', '=', self.patient_id.id), ('product_id', '=', product.id), ('state', '=', 'executed')],
            order='date_executed desc', limit=1)
        dates = [line.date_executed] if line else []
        if product.result_type_ids:
            obs = self.env['medical.observation'].sudo().search([
                ('patient_id', '=', self.patient_id.id), ('type_id', 'in', product.result_type_ids.ids)],
                order='date desc', limit=1)
            if obs:
                dates.append(obs.date.date())
        return max(dates) if dates else False

    def _due_tests(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        due = self.env['medical.chronic.test']
        for test in self.program_id.test_ids:
            last = self._last_done(test.product_id)
            if not last or last + relativedelta(months=test.interval_months) <= today:
                due |= test
        return due

    def _compute_tests_due(self):
        for enrollment in self:
            due = enrollment._due_tests() if enrollment.state == 'active' else self.env['medical.chronic.test']
            enrollment.tests_due = ', '.join(due.product_id.mapped('name')) or False
            enrollment.has_tests_due = bool(due)

    def _search_has_tests_due(self, operator, value):
        if operator in ('in', 'not in'):
            value = True in value
            operator = '=' if operator == 'in' else '!='
        ids = [e.id for e in self.search([('state', '=', 'active')]) if e._due_tests()]
        positive = (operator == '=') == bool(value)
        return [('id', 'in' if positive else 'not in', ids)]

    # ----------------------------------------------------------- control
    def _refresh_control(self):
        """Compare the latest value of each target with the program target (results from the last 6 months)."""
        since = fields.Datetime.subtract(fields.Datetime.now(), months=6)
        Observation = self.env['medical.observation'].sudo()
        for enrollment in self:
            met, missed, lines = 0, 0, []
            for target in enrollment.program_id.target_ids:
                obs = Observation.search([('patient_id', '=', enrollment.patient_id.id),
                                          ('type_id', '=', target.type_id.id), ('date', '>=', since)],
                                         order='date desc', limit=1)
                if not obs:
                    continue
                ok = target._met(obs.value)
                met += ok
                missed += not ok
                lines.append(f"{'✓' if ok else '✗'} {target.type_id.name}: {obs.value:g} {target.type_id.unit or ''} "
                             f"({_('target')} {target.display_name.replace(target.type_id.name, '').strip()})")
            if not met and not missed:
                status = 'unknown'
            elif not missed:
                status = 'controlled'
            elif not met:
                status = 'uncontrolled'
            else:
                status = 'partial'
            enrollment.write({'control_status': status, 'control_detail': '\n'.join(lines) or False})

    # ------------------------------------------------------------ actions
    def action_order_due_tests(self):
        self.ensure_one()
        due = self._due_tests().product_id.filtered(lambda p: p.procedure_type in ('lab', 'imaging', 'test'))
        by_type = {}
        for product in due:
            by_type.setdefault(product.procedure_type, self.env['product.product'])
            by_type[product.procedure_type] |= product
        orders = self.env['medical.order']
        for order_type, products in by_type.items():
            orders |= self.env['medical.order'].create({
                'patient_id': self.patient_id.id,
                'doctor_id': (self.doctor_id or self.env.user.employee_id).id,
                'order_type': order_type,
                'clinical_info': _("%s periodic tests", self.program_id.name),
                'line_ids': [(0, 0, {'product_id': p.id}) for p in products],
            })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'medical.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', orders.ids)],
            'name': _("Ordered Tests"),
        }

    def action_mark_reviewed(self):
        self.write({'last_review_date': fields.Date.context_today(self)})
        self._refresh_control()
        prefix = _("Review due")
        self.activity_ids.filtered(lambda a: a.summary and a.summary.startswith(prefix)).action_feedback()

    def action_pause(self):
        self.write({'state': 'paused'})

    def action_resume(self):
        self.write({'state': 'active'})

    def action_stop(self):
        self.write({'state': 'stopped'})

    @api.model
    def _cron_chronic_recall(self):
        """Daily: refresh control status and remind the doctor of reviews that are due."""
        enrollments = self.search([('state', '=', 'active')])
        enrollments._refresh_control()
        for enrollment in enrollments.filtered('is_review_due'):
            summary = _("Review due: %s", enrollment.display_name)
            if not enrollment.activity_ids.filtered(lambda a: a.summary == summary):
                user = enrollment.doctor_id.user_id or self.env.ref('base.user_admin')
                enrollment.activity_schedule('mail.mail_activity_data_call', date_deadline=enrollment.next_review_date,
                                             user_id=user.id, summary=summary,
                                             note=_("Call the patient to book the follow-up. Due tests: %s",
                                                    enrollment.tests_due or _("none")))


class MedicalObservation(models.Model):
    _inherit = 'medical.observation'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        enrollments = self.env['medical.chronic.enrollment'].sudo().search(
            [('patient_id', 'in', records.patient_id.ids), ('state', '=', 'active'),
             ('program_id.target_ids.type_id', 'in', records.type_id.ids)])
        enrollments._refresh_control()
        return records


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    chronic_enrollment_ids = fields.One2many(related='patient_id.chronic_enrollment_ids', string='Chronic Programs')

    def action_done(self):
        res = super().action_done()
        # a visit with the program doctor counts as the program review
        for visit in self:
            enrollments = visit.patient_id.chronic_enrollment_ids.filtered(
                lambda e: e.state == 'active' and (not e.doctor_id or e.doctor_id == visit.doctor_id))
            enrollments.sudo().write({'last_review_date': visit.start.date()})
        return res

    def _update_problem_list(self):
        res = super()._update_problem_list()
        Enrollment = self.env['medical.chronic.enrollment'].sudo()
        for visit in self.sudo():
            diagnoses = visit.diagnosis_ids.filtered(lambda d: d.certainty == 'confirmed').diagnosis_id
            programs = self.env['medical.chronic.program'].sudo().search([('diagnosis_ids', 'in', diagnoses.ids)])
            for program in programs - visit.patient_id.chronic_enrollment_ids.program_id:
                Enrollment.create({'patient_id': visit.patient_id.id, 'program_id': program.id,
                                   'doctor_id': visit.doctor_id.id, 'start_date': visit.start.date(),
                                   'last_review_date': visit.start.date()})
        return res

    def _brief_specialty_facts(self):
        facts = super()._brief_specialty_facts()
        enrollments = self.patient_id.sudo().chronic_enrollment_ids.filtered(lambda e: e.state == 'active')
        if enrollments:
            status = dict(enrollments._fields['control_status']._description_selection(self.env))
            facts.append((_("Chronic programs"), [
                f"{e.program_id.name}: {status.get(e.control_status)}" + (f" — {_('due')}: {e.tests_due}" if e.tests_due else '')
                for e in enrollments]))
        return facts


class ResPartner(models.Model):
    _inherit = 'res.partner'

    chronic_enrollment_ids = fields.One2many('medical.chronic.enrollment', 'patient_id', string='Chronic Programs')
