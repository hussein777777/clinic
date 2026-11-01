from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .product_template import DOSAGE_FORMS, ROUTES

FREQUENCIES = [
    ('once', 'Once'),
    ('od', 'Once daily'),
    ('bid', 'Twice daily'),
    ('tid', 'Three times daily'),
    ('qid', 'Four times daily'),
    ('q8h', 'Every 8 hours'),
    ('q6h', 'Every 6 hours'),
    ('hs', 'At bedtime'),
    ('weekly', 'Once a week'),
    ('prn', 'When needed'),
]
# administrations per day, used to compute the quantity to dispense
PER_DAY = {'once': 0, 'od': 1, 'bid': 2, 'tid': 3, 'qid': 4, 'q8h': 3, 'q6h': 4, 'hs': 1, 'weekly': 1 / 7, 'prn': 0}

TIMING = [
    ('any', 'Any time'),
    ('before_meal', 'Before meals'),
    ('after_meal', 'After meals'),
    ('with_meal', 'With meals'),
    ('empty_stomach', 'On an empty stomach'),
]


class MedicalPrescription(models.Model):
    _name = 'medical.prescription'
    _description = 'Prescription'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _('New'))
    patient_id = fields.Many2one('res.partner', required=True, index=True, domain=[('is_patient', '=', True)])
    visit_id = fields.Many2one('medical.visit', index='btree_not_null', ondelete='set null')
    doctor_id = fields.Many2one('hr.employee', string='Doctor', required=True, domain=[('is_practitioner', '=', True)],
                                default=lambda self: self.env.user.employee_id.filtered('is_practitioner'))
    department_id = fields.Many2one('medical.department', compute='_compute_department_id', store=True, readonly=False,
                                    precompute=True)
    date = fields.Date(default=fields.Date.context_today, required=True)
    line_ids = fields.One2many('medical.prescription.line', 'prescription_id', string='Medicines', copy=True)
    state = fields.Selection([('draft', 'Draft'), ('done', 'Issued'), ('cancelled', 'Cancelled')],
                             default='draft', required=True, tracking=True, copy=False)
    allergy_warning = fields.Char(compute='_compute_allergy_warning')
    patient_weight = fields.Float(string='Weight (kg)', digits=(6, 2),
                                  help='Taken from the visit vitals; used for weight-based doses.')
    note = fields.Text(string='Advice to the Patient')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    @api.depends('visit_id', 'doctor_id')
    def _compute_department_id(self):
        for prescription in self:
            prescription.department_id = prescription.visit_id.department_id or \
                prescription.doctor_id.sudo().medical_department_ids[:1] or prescription.department_id

    @api.depends('line_ids.product_id', 'patient_id')
    def _compute_allergy_warning(self):
        for prescription in self:
            conflicts = prescription.patient_id._medical_allergy_conflicts(prescription.line_ids.product_id) \
                if prescription.patient_id else {}
            prescription.allergy_warning = ', '.join(
                f"{p.name} ({', '.join(a.mapped('name'))})" for p, a in conflicts.items()) or False

    @api.onchange('visit_id')
    def _onchange_visit_id(self):
        if self.visit_id:
            self.patient_weight = self.visit_id.sudo().weight

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('medical.prescription') or _('New')
            if vals.get('visit_id') and not vals.get('patient_weight'):
                vals['patient_weight'] = self.env['medical.visit'].browse(vals['visit_id']).sudo().weight
        return super().create(vals_list)

    def action_issue(self):
        for prescription in self:
            if not prescription.line_ids:
                raise UserError(_("Add at least one medicine."))
            if prescription.allergy_warning and not self.env.context.get('medical_allergy_ack'):
                raise UserError(_("Allergy conflict: %s. Change the medicine or confirm the override.",
                                  prescription.allergy_warning))
        self.write({'state': 'done'})

    def action_issue_override(self):
        self.message_post(body=_("Issued despite the allergy warning: %s", self.mapped('allergy_warning')))
        return self.with_context(medical_allergy_ack=True).action_issue()

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_print(self):
        return self.env.ref('medical_core.action_report_medical_prescription').report_action(self)


class MedicalPrescriptionLine(models.Model):
    _name = 'medical.prescription.line'
    _description = 'Prescription Line'
    _order = 'prescription_id, sequence, id'

    prescription_id = fields.Many2one('medical.prescription', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', string='Medicine', domain=[('is_medicine', '=', True)])
    name = fields.Char(string='Medicine Name', compute='_compute_name', store=True, readonly=False, required=True,
                       precompute=True)
    dosage_form = fields.Selection(DOSAGE_FORMS, compute='_compute_from_product', store=True, readonly=False,
                                   precompute=True)
    route = fields.Selection(ROUTES, compute='_compute_from_product', store=True, readonly=False, precompute=True)
    dose = fields.Char(help='e.g. 1 tablet, 5 ml, 2 drops, 500 mg')
    frequency = fields.Selection(FREQUENCIES, default='bid', required=True)
    timing = fields.Selection(TIMING, default='any')
    duration_days = fields.Integer(string='Days', default=5)
    quantity = fields.Char(string='Quantity to Dispense')
    instructions = fields.Char()
    is_chronic = fields.Boolean(string='Long-term', help='Repeat medicine for a chronic condition.')

    @api.depends('product_id')
    def _compute_name(self):
        for line in self:
            if line.product_id:
                parts = [line.product_id.name]
                if line.product_id.active_ingredient and line.product_id.active_ingredient not in line.product_id.name:
                    parts.append(f"({line.product_id.active_ingredient})")
                if line.product_id.strength:
                    parts.append(line.product_id.strength)
                line.name = ' '.join(parts)

    @api.depends('product_id')
    def _compute_from_product(self):
        for line in self:
            if line.product_id:
                line.dosage_form = line.product_id.dosage_form or line.dosage_form
                line.route = line.product_id.default_route or line.route

    def _administrations(self):
        self.ensure_one()
        return PER_DAY.get(self.frequency, 0) * (self.duration_days or 0)

    def _sig(self):
        """One readable line: dose, frequency, timing and duration."""
        self.ensure_one()
        freq = dict(self._fields['frequency']._description_selection(self.env)).get(self.frequency, '')
        timing = dict(self._fields['timing']._description_selection(self.env)).get(self.timing, '') \
            if self.timing and self.timing != 'any' else ''
        parts = [self.dose or '', freq, timing]
        if self.duration_days and self.frequency != 'once':
            parts.append(_("for %s days", self.duration_days))
        return ' — '.join(p for p in parts if p)
