from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MedicalConsent(models.Model):
    _name = 'medical.consent'
    _description = 'Informed Consent'
    _inherit = ['mail.thread']
    _order = 'id desc'

    patient_id = fields.Many2one('res.partner', required=True, index=True, domain=[('is_patient', '=', True)])
    product_id = fields.Many2one('product.product', string='Procedure', required=True)
    doctor_id = fields.Many2one('hr.employee', string='Doctor', domain=[('is_practitioner', '=', True)])
    visit_id = fields.Many2one('medical.visit', ondelete='set null')
    consent_text = fields.Html(compute='_compute_consent_text', store=True, readonly=False, precompute=True,
                               sanitize=True)
    signed_by = fields.Char(string='Signed by', help='Patient or legal guardian name.')
    relationship = fields.Selection([('self', 'Patient'), ('parent', 'Parent'), ('guardian', 'Legal Guardian'),
                                     ('spouse', 'Spouse'), ('other', 'Other')], default='self')
    signature = fields.Binary(copy=False, attachment=True)
    signed_on = fields.Datetime(readonly=True, copy=False)
    signed = fields.Boolean(compute='_compute_signed', store=True)
    witness_id = fields.Many2one('res.users', string='Witness', default=lambda self: self.env.user)

    @api.depends('product_id')
    def _compute_consent_text(self):
        for consent in self:
            if consent.consent_text:
                continue
            text = consent.product_id.consent_text
            consent.consent_text = text or _(
                "<p>I agree to undergo <b>%(proc)s</b>. The doctor explained the procedure, its expected benefits, "
                "possible risks and complications, and the alternatives including no treatment. I had the chance to "
                "ask questions and all my questions were answered.</p>", proc=consent.product_id.name or '')

    @api.depends('signature', 'signed_on')
    def _compute_signed(self):
        for consent in self:
            consent.signed = bool(consent.signature and consent.signed_on)

    @api.depends('product_id', 'patient_id')
    def _compute_display_name(self):
        for consent in self:
            consent.display_name = _("Consent: %(proc)s", proc=consent.product_id.name or '')

    def action_sign(self):
        for consent in self:
            if not consent.signature:
                raise UserError(_("The patient must sign first."))
            consent.write({'signed_on': fields.Datetime.now(),
                           'signed_by': consent.signed_by or consent.patient_id.name})
        return {'type': 'ir.actions.act_window_close'}

    def action_print(self):
        return self.env.ref('medical_core.action_report_medical_consent').report_action(self)
