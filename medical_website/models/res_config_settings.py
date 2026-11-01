from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    clinic_name = fields.Char(related='website_id.clinic_name', readonly=False)
    clinic_tagline = fields.Char(related='website_id.clinic_tagline', readonly=False)
    clinic_about = fields.Html(related='website_id.clinic_about', readonly=False)
    clinic_hours_text = fields.Text(related='website_id.clinic_hours_text', readonly=False)
    clinic_whatsapp = fields.Char(related='website_id.clinic_whatsapp', readonly=False)
    clinic_map_url = fields.Char(related='website_id.clinic_map_url', readonly=False)
    booking_auto_confirm = fields.Boolean(related='website_id.booking_auto_confirm', readonly=False)
    booking_lead_hours = fields.Integer(related='website_id.booking_lead_hours', readonly=False)
    booking_days_ahead = fields.Integer(related='website_id.booking_days_ahead', readonly=False)
    booking_email_required = fields.Boolean(related='website_id.booking_email_required', readonly=False)
