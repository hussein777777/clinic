from odoo import api, fields, models

ICONS = [
    ('fa-stethoscope', 'Stethoscope'),
    ('fa-heartbeat', 'Heartbeat'),
    ('fa-user-md', 'Doctor'),
    ('fa-smile-o', 'Smile (dentistry)'),
    ('fa-eye', 'Eye'),
    ('fa-child', 'Child'),
    ('fa-female', 'Woman'),
    ('fa-wheelchair', 'Mobility'),
    ('fa-medkit', 'Medical kit'),
    ('fa-flask', 'Laboratory'),
    ('fa-hand-paper-o', 'Skin'),
    ('fa-bolt', 'Nerves'),
]


class MedicalDepartment(models.Model):
    _inherit = 'medical.department'

    website_published = fields.Boolean(string='Show on Website', copy=False)
    website_sequence = fields.Integer(default=10)
    website_intro = fields.Char(string='One-line Introduction', translate=True,
                                help='Shown under the clinic name on the website.')
    website_description = fields.Html(string='Website Description', translate=True, sanitize=True)
    website_icon = fields.Selection(ICONS, default='fa-stethoscope')
    online_booking = fields.Boolean(string='Online Booking', default=True,
                                    help='Patients can book this clinic from the website.')
    booking_duration = fields.Float(string='Online Slot (h)', default=0.5,
                                    help='Length of a visit booked from the website.')
    website_url = fields.Char(compute='_compute_website_url')

    def _compute_website_url(self):
        slug = self.env['ir.http']._slug
        for department in self:
            department.website_url = f'/clinic/department/{slug(department)}' if department.id else False


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    website_published = fields.Boolean(string='Show on Website', copy=False, groups='hr.group_hr_user,medical_core.group_medical_user')
    website_booking = fields.Boolean(string='Bookable Online', default=True,
                                     groups='hr.group_hr_user,medical_core.group_medical_user')
    website_headline = fields.Char(string='Website Headline', translate=True,
                                   groups='hr.group_hr_user,medical_core.group_medical_user',
                                   help='e.g. Consultant orthodontist, 15 years of experience.')
    website_bio = fields.Html(string='Website Biography', translate=True, sanitize=True,
                              groups='hr.group_hr_user,medical_core.group_medical_user')
    website_languages = fields.Char(string='Spoken Languages', translate=True,
                                    groups='hr.group_hr_user,medical_core.group_medical_user')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    medical_website_published = fields.Boolean(string='Show on Clinic Website')
    medical_website_description = fields.Text(string='Website Description', translate=True)
    medical_website_show_price = fields.Boolean(string='Show Price on Website')


class Website(models.Model):
    _inherit = 'website'

    clinic_name = fields.Char(string='Clinic Name', translate=True,
                              help='Name shown on the clinic website, per language. Empty: the company name.')
    clinic_tagline = fields.Char(string='Clinic Tagline', translate=True)
    clinic_about = fields.Html(string='About the Clinic', translate=True, sanitize=True)
    clinic_hours_text = fields.Text(string='Opening Hours', translate=True,
                                    help='As patients should read it, one line per day group.')
    clinic_whatsapp = fields.Char(string='WhatsApp Number', help='International format, e.g. +201001234567.')
    clinic_map_url = fields.Char(string='Map Link', help='A Google Maps (or similar) link to the clinic.')
    booking_auto_confirm = fields.Boolean(string='Confirm Online Bookings Automatically')
    booking_lead_hours = fields.Integer(string='Book at Least (hours ahead)', default=2)
    booking_days_ahead = fields.Integer(string='Book up to (days ahead)', default=21)
    booking_email_required = fields.Boolean(string='E-mail Required to Book')

    @api.model
    def _clinic_whatsapp_link(self):
        digits = ''.join(ch for ch in (self.clinic_whatsapp or '') if ch.isdigit())
        return f'https://wa.me/{digits}' if digits else False


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    booking_source = fields.Selection([
        ('reception', 'Reception'),
        ('phone', 'Phone'),
        ('website', 'Website'),
    ], default='reception', index=True, copy=False, tracking=True)
    booking_token = fields.Char(copy=False, index='btree_not_null', groups='base.group_system,medical_core.group_medical_user')
    booking_name = fields.Char(string='Name Given Online', copy=False,
                               help='The name typed in the online form; the visit may be linked to an existing patient.')
    booking_note = fields.Text(string='Patient Message', copy=False)
    booking_ip = fields.Char(copy=False, groups='base.group_system')
    booking_cancelled_online = fields.Boolean(string='Cancelled by the Patient', copy=False, readonly=True)
