import re
import uuid
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import email_normalize

ACTIVE_STATES = ('draft', 'confirmed', 'checked_in', 'in_consult')
PHONE_DIGITS = re.compile(r'\D')
MAX_PENDING_PER_PHONE = 2
MAX_PER_ADDRESS_HOUR = 6


def phone_key(phone):
    """Digits of a phone number without the country prefix noise: the last 9 digits identify it."""
    digits = PHONE_DIGITS.sub('', phone or '')
    return digits[-9:] if len(digits) >= 9 else digits


class MedicalWebsiteBooking(models.AbstractModel):
    _name = 'medical.website.booking'
    _description = 'Online booking engine'

    # ------------------------------------------------------------------ catalog
    @api.model
    def _website(self):
        return self.env['website'].get_current_website()

    @api.model
    def _departments(self):
        """Clinics shown on the website (sudo: called for anonymous visitors, only public fields are used)."""
        company = self._website().company_id
        return self.env['medical.department'].sudo().search(
            [('website_published', '=', True), ('company_id', 'in', (False, company.id))],
            order='website_sequence, sequence, name')

    @api.model
    def _doctors(self, department=None, bookable=False):
        domain = [('is_practitioner', '=', True), ('website_published', '=', True),
                  ('company_id', 'in', (False, self._website().company_id.id))]
        if department:
            domain.append(('medical_department_ids', 'in', department.ids))
        else:
            domain.append(('medical_department_ids.website_published', '=', True))
        doctors = self.env['hr.employee'].sudo().search(domain, order='name')
        if bookable:
            doctors = doctors.filtered(lambda d: d.website_booking and d.resource_calendar_id)
        return doctors

    @api.model
    def _services(self, department):
        return self.env['product.template'].sudo().search([
            ('medical_website_published', '=', True), ('is_medical_procedure', '=', True),
            ('specialty_id.department_id', '=', department.id)], order='name')

    # -------------------------------------------------------------------- slots
    @api.model
    def _tz(self, doctor=None):
        name = (doctor and (doctor.sudo().tz or doctor.sudo().resource_calendar_id.tz)) \
            or self._website().sudo().company_id.resource_calendar_id.tz or self.env.user.tz or 'UTC'
        return pytz.timezone(name)

    @api.model
    def _busy(self, doctors, start, stop):
        """Busy intervals (naive UTC) per doctor id: their active visits and the visits in their default room."""
        Visit = self.env['medical.visit'].sudo()
        busy = {doctor.id: [] for doctor in doctors}
        visits = Visit.search([('state', 'in', ACTIVE_STATES), ('start', '<', stop), ('stop', '>', start),
                               '|', ('doctor_id', 'in', doctors.ids), ('room_id', 'in', doctors.default_room_id.ids)])
        for visit in visits:
            for doctor in doctors:
                if visit.doctor_id == doctor or (doctor.default_room_id and visit.room_id == doctor.default_room_id):
                    busy[doctor.id].append((visit.start, visit.stop))
        return busy

    @api.model
    def _slots(self, department, doctor=None, day_from=None, days=None, now=None):
        """Free slots, grouped by local day: {date: [{'start': 'YYYY-MM-DD HH:MM:SS' (UTC), 'time': 'HH:MM',
        'doctor_ids': [...]}]}. A slot is free when the doctor works then and has no visit overlapping it."""
        website = self._website()
        now = now or fields.Datetime.now()
        days = days or website.booking_days_ahead or 21
        earliest = now + timedelta(hours=website.booking_lead_hours or 0)
        doctors = doctor.sudo() if doctor else self._doctors(department, bookable=True)
        doctors = doctors.filtered(lambda d: d.website_booking and d.resource_calendar_id
                                   and department in d.medical_department_ids)
        step = timedelta(hours=department.booking_duration or department.default_visit_duration or 0.5)
        result = {}
        if not doctors:
            return result
        tz = self._tz(doctors[0])
        local_today = pytz.utc.localize(now).astimezone(tz).date()
        first_day = max(day_from or local_today, local_today)
        start_local = tz.localize(datetime.combine(first_day, time.min))
        end_local = tz.localize(datetime.combine(local_today + timedelta(days=days), time.max))
        busy = self._busy(doctors, start_local.astimezone(pytz.utc).replace(tzinfo=None),
                          end_local.astimezone(pytz.utc).replace(tzinfo=None))
        for doc in doctors:
            calendar = doc.resource_calendar_id
            doc_tz = self._tz(doc)
            intervals = calendar._work_intervals_batch(start_local, end_local, resources=doc.resource_id,
                                                       tz=doc_tz)[doc.resource_id.id]
            for begin, end, _meta in intervals:
                slot = begin
                while slot + step <= end:
                    s_utc = slot.astimezone(pytz.utc).replace(tzinfo=None)
                    e_utc = s_utc + step
                    if s_utc >= earliest and not any(b0 < e_utc and b1 > s_utc for b0, b1 in busy[doc.id]):
                        local = s_utc.replace(tzinfo=pytz.utc).astimezone(tz)
                        day = result.setdefault(local.date(), {})
                        key = fields.Datetime.to_string(s_utc)
                        entry = day.setdefault(key, {'start': key, 'time': local.strftime('%H:%M'), 'doctor_ids': []})
                        entry['doctor_ids'].append(doc.id)
                    slot += step
        return {day: sorted(slots.values(), key=lambda s: s['start']) for day, slots in sorted(result.items())}

    @api.model
    def _next_slot(self, days=7):
        """Earliest free slot over all bookable clinics, for the website's front page."""
        best = None
        for department in self._departments().filtered('online_booking'):
            slots = self._slots(department, days=days)
            for day, entries in slots.items():
                if entries:
                    candidate = (entries[0]['start'], day, entries[0], department)
                    if not best or candidate[0] < best[0]:
                        best = candidate
                    break
        if not best:
            return None
        start, day, entry, department = best
        doctor = self.env['hr.employee'].sudo().browse(entry['doctor_ids'][0])
        return {'day': day, 'time': entry['time'], 'department': department, 'doctor': doctor}

    # ------------------------------------------------------------------ booking
    @api.model
    def _check_slot(self, department, doctor, start):
        """Doctor `doctor` (or the first free one if empty) for a slot starting at naive-UTC `start`."""
        reference = doctor or self._doctors(department, bookable=True)[:1]
        local_day = pytz.utc.localize(start).astimezone(self._tz(reference or None)).date()
        slots = self._slots(department, doctor=doctor or None, day_from=local_day, days=(local_day - fields.Date.context_today(self)).days + 1)
        key = fields.Datetime.to_string(start)
        for entry in slots.get(local_day, []):
            if entry['start'] == key:
                return self.env['hr.employee'].sudo().browse(entry['doctor_ids'][0] if not doctor else doctor.id)
        raise ValidationError(_("This time was just taken. Please choose another one."))

    @api.model
    def _find_or_create_patient(self, name, phone, email=False, birthdate=False, gender=False):
        Partner = self.env['res.partner'].sudo()
        key = phone_key(phone)
        if len(key) >= 7:
            # phones are stored as typed ("+218 92 434 7217"): match the digits whatever the spacing
            pattern = '%' + '%'.join(key[-7:]) + '%'
            for candidate in Partner.search([('is_patient', '=', True), ('phone', '=ilike', pattern)], limit=50):
                if phone_key(candidate.phone) == key:
                    return candidate
        vals = {'name': name, 'phone': phone, 'is_patient': True, 'email': email or False}
        if self.env.lang and self.env['res.lang']._lang_get(self.env.lang):
            vals['lang'] = self.env.lang
        if birthdate:
            vals['birthdate'] = birthdate
        if gender in ('male', 'female'):
            vals['gender'] = gender
        return Partner.create(vals)

    @api.model
    def book(self, values, remote_addr=None):
        """Create a website booking from the form values; returns the visit (sudo)."""
        website = self._website()
        if values.get('website_hp'):
            raise ValidationError(_("The booking could not be sent."))
        name = (values.get('name') or '').strip()
        phone = (values.get('phone') or '').strip()
        email = (values.get('email') or '').strip()
        if len(name) < 3:
            raise ValidationError(_("Please write the patient's full name."))
        if len(PHONE_DIGITS.sub('', phone)) < 8:
            raise ValidationError(_("Please write a phone number we can call."))
        if email and not email_normalize(email):
            raise ValidationError(_("The e-mail address is not valid."))
        if website.booking_email_required and not email:
            raise ValidationError(_("Please write your e-mail address."))
        if not values.get('consent'):
            raise ValidationError(_("Please accept that the clinic stores your details to manage the visit."))
        department = self._departments().filtered(lambda d: d.id == int(values.get('department_id') or 0)
                                                  and d.online_booking)
        if not department:
            raise ValidationError(_("This clinic does not take online bookings."))
        doctor = self.env['hr.employee'].sudo()
        if values.get('doctor_id'):
            doctor = self._doctors(department, bookable=True).filtered(lambda d: d.id == int(values['doctor_id']))
            if not doctor:
                raise ValidationError(_("This doctor cannot be booked online."))
        try:
            start = fields.Datetime.to_datetime(values.get('start'))
        except ValueError:
            start = None
        if not start:
            raise ValidationError(_("Please choose a time."))

        Visit = self.env['medical.visit'].sudo()
        since = fields.Datetime.now() - timedelta(hours=1)
        if remote_addr and Visit.search_count([('booking_ip', '=', remote_addr), ('create_date', '>=', since)]) >= MAX_PER_ADDRESS_HOUR:
            raise ValidationError(_("Too many bookings from this connection. Please call the clinic."))
        key = phone_key(phone)
        pending = Visit.search([('booking_source', '=', 'website'), ('state', 'in', ('draft', 'confirmed')),
                                ('start', '>=', fields.Datetime.now())])
        if len(pending.filtered(lambda v: phone_key(v.patient_id.phone) == key)) >= MAX_PENDING_PER_PHONE:
            raise ValidationError(_("This phone number already has upcoming bookings. Please call the clinic to add another."))

        # serialise bookings on the doctors concerned, then re-check the slot
        candidates = doctor or self._doctors(department, bookable=True)
        self.env.cr.execute("SELECT id FROM hr_employee WHERE id IN %s FOR UPDATE", [tuple(candidates.ids)])
        doctor = self._check_slot(department, doctor, start)
        patient = self._find_or_create_patient(name, phone, email=email, birthdate=values.get('birthdate') or False,
                                               gender=values.get('gender'))
        returning = Visit.search_count([('patient_id', '=', patient.id), ('state', 'in', ('done', 'invoiced'))])
        visit = Visit.create({
            'patient_id': patient.id,
            'doctor_id': doctor.id,
            'department_id': department.id,
            'room_id': doctor.default_room_id.id,
            'start': start,
            'duration': department.booking_duration or department.default_visit_duration or 0.5,
            'visit_type': 'follow_up' if returning else 'new',
            'booking_source': 'website',
            'booking_token': uuid.uuid4().hex,
            'booking_name': name,
            'booking_note': (values.get('note') or '').strip()[:1000] or False,
            'booking_ip': remote_addr,
            'company_id': department.company_id.id or website.company_id.id,
        })
        if website.booking_auto_confirm:
            visit.action_confirm()
        visit.message_post(body=_("Booked online by %(name)s (%(phone)s).", name=name, phone=phone),
                           subtype_xmlid='mail.mt_note')
        if email:
            template = self.env.ref('medical_website.mail_template_booking_received', raise_if_not_found=False)
            if template:
                template.sudo().with_context(lang=self.env.lang).send_mail(
                    visit.id, email_values={'email_to': email}, force_send=False)
        return visit

    @api.model
    def _visit_by_token(self, token):
        if not token or len(token) != 32:
            return self.env['medical.visit']
        return self.env['medical.visit'].sudo().search([('booking_token', '=', token)], limit=1)

    @api.model
    def cancel(self, token):
        visit = self._visit_by_token(token)
        if not visit:
            raise UserError(_("Booking not found."))
        if visit.state not in ('draft', 'confirmed') or visit.start <= fields.Datetime.now():
            raise UserError(_("This booking can no longer be cancelled online. Please call the clinic."))
        visit.write({'booking_cancelled_online': True})
        visit.action_cancel()
        visit.message_post(body=_("Cancelled by the patient from the booking page."), subtype_xmlid='mail.mt_note')
        return visit
