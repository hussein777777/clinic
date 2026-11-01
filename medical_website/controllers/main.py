import base64

import pytz
from babel.dates import format_date

from odoo import fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request


class MedicalWebsite(http.Controller):

    # ---------------------------------------------------------------- helpers
    def _engine(self):
        return request.env['medical.website.booking']

    def _locale(self):
        return (request.lang.code or 'en_US').replace('-', '_')

    def _day_label(self, day, pattern):
        try:
            return format_date(day, pattern, locale=self._locale())
        except Exception:  # noqa: BLE001 - unknown locale
            return day.strftime('%a %d %b' if pattern != 'EEEE' else '%A')

    def _unslug_record(self, model, slug, records):
        _name, record_id = request.env['ir.http']._unslug(slug)
        record = records.filtered(lambda r: r.id == record_id)
        if not record:
            raise request.not_found()
        return record

    def _common(self):
        website = request.website
        company = website.company_id
        return {
            'clinic_company': company.sudo(),
            'clinic_whatsapp_url': website._clinic_whatsapp_link(),
            'clinic_departments': self._engine()._departments(),
            'slug': request.env['ir.http']._slug,
            'day_label': self._day_label,
            'has_photo': self._has_photo,
            'initials': self._initials,
        }

    @staticmethod
    def _has_photo(doctor):
        """True for an uploaded photo; Odoo's generated initials avatar (an SVG) does not count."""
        image = doctor.sudo().image_1920
        if not image:
            return False
        if isinstance(image, str):
            image = image.encode()
        head = base64.b64decode(image[:64])  # 64 base64 characters = 48 bytes, enough to see the format
        return not head.lstrip().startswith((b'<svg', b'<?xml'))

    @staticmethod
    def _initials(name):
        """Two letters for a doctor without a photo, skipping the title (Dr., د., أ.)."""
        words = [w for w in (name or '').replace('.', '. ').split()
                 if w.rstrip('.').lower() not in ('dr', 'د', 'أ', 'prof', 'أ.د')]
        letters = []
        for word in words[:2]:
            if word.startswith('ال') and len(word) > 3:
                word = word[2:]  # Arabic article: Al-Obeidi -> ع, not ا
            letters.append(word[0])
        arabic = any('\u0600' <= ch <= '\u06ff' for ch in letters)
        return (' ' if arabic else '').join(letters).upper()

    # ------------------------------------------------------------------ pages
    @http.route('/clinic', type='http', auth='public', website=True, sitemap=True)
    def clinic(self, **kw):
        engine = self._engine()
        values = self._common()
        values.update({
            'doctors': engine._doctors(),
            'next_slot': engine._next_slot(),
        })
        return request.render('medical_website.clinic_home', values)

    @http.route('/clinic/department/<string:slug>', type='http', auth='public', website=True, sitemap=True)
    def department(self, slug, **kw):
        engine = self._engine()
        department = self._unslug_record('medical.department', slug, engine._departments())
        values = self._common()
        values.update({
            'department': department,
            'doctors': engine._doctors(department),
            'services': engine._services(department),
            'currency': request.website.company_id.currency_id,
        })
        return request.render('medical_website.clinic_department', values)

    @http.route('/clinic/doctors', type='http', auth='public', website=True, sitemap=True)
    def doctors(self, **kw):
        values = self._common()
        values['doctors'] = self._engine()._doctors()
        return request.render('medical_website.clinic_doctors', values)

    @http.route('/clinic/doctor/<string:slug>', type='http', auth='public', website=True, sitemap=True)
    def doctor(self, slug, **kw):
        doctor = self._unslug_record('hr.employee', slug, self._engine()._doctors())
        values = self._common()
        values['doctor'] = doctor
        values['doctor_departments'] = doctor.medical_department_ids.filtered('website_published')
        return request.render('medical_website.clinic_doctor', values)

    @http.route('/clinic/doctor/<int:doctor_id>/photo', type='http', auth='public', website=True, sitemap=False)
    def doctor_photo(self, doctor_id, **kw):
        doctor = self._engine()._doctors().filtered(lambda d: d.id == doctor_id)
        if not doctor:
            raise request.not_found()
        field = 'image_512' if doctor.image_512 else 'avatar_512'
        return request.env['ir.binary']._get_image_stream_from(doctor, field).get_response()

    # ---------------------------------------------------------------- booking
    def _booking_values(self, form=None, error=None):
        engine = self._engine()
        departments = engine._departments().filtered('online_booking')
        form = dict(form or {})
        department_id = int(form.get('department_id') or form.get('department') or 0)
        if department_id not in departments.ids:
            department_id = departments[:1].id if len(departments) == 1 else 0
        doctors = engine._doctors(departments, bookable=True)
        values = self._common()
        values.update({
            'departments': departments,
            'booking_doctors': doctors,
            'form': form,
            'selected_department': department_id,
            'selected_doctor': int(form.get('doctor_id') or form.get('doctor') or 0),
            'selected_start': form.get('start') or '',
            'error': error,
            'email_required': request.website.booking_email_required,
        })
        return values

    @http.route('/appointment', type='http', auth='public', website=True, sitemap=True)
    def appointment(self, **kw):
        return request.render('medical_website.booking_page', self._booking_values(kw))

    @http.route('/appointment/slots', type='jsonrpc', auth='public', website=True, readonly=True)
    def appointment_slots(self, department_id, doctor_id=None):
        engine = self._engine()
        department = engine._departments().filtered(lambda d: d.id == int(department_id or 0) and d.online_booking)
        if not department:
            return {'days': []}
        doctor = None
        if doctor_id:
            doctor = engine._doctors(department, bookable=True).filtered(lambda d: d.id == int(doctor_id))
            if not doctor:
                return {'days': []}
        slots = engine._slots(department, doctor=doctor)
        return {'days': [{
            'date': fields.Date.to_string(day),
            'weekday': self._day_label(day, 'EEEE'),
            'label': self._day_label(day, 'd MMM'),
            'slots': [{'start': s['start'], 'time': s['time']} for s in entries],
        } for day, entries in slots.items() if entries]}

    @http.route('/appointment/submit', type='http', auth='public', website=True, methods=['POST'], sitemap=False)
    def appointment_submit(self, **post):
        try:
            visit = self._engine().book(post, remote_addr=request.httprequest.remote_addr)
        except (ValidationError, UserError) as error:
            return request.render('medical_website.booking_page', self._booking_values(post, error=str(error)))
        return request.redirect(f'/appointment/booking/{visit.booking_token}')

    @http.route('/appointment/booking/<string:token>', type='http', auth='public', website=True, sitemap=False)
    def appointment_booking(self, token, **kw):
        visit = self._engine()._visit_by_token(token)
        if not visit:
            raise request.not_found()
        tz = self._engine()._tz(visit.doctor_id)
        local = pytz.utc.localize(visit.start).astimezone(tz)
        values = self._common()
        values.update({
            'visit': visit,
            'visit_day': self._day_label(local.date(), 'EEEE d MMMM y'),
            'visit_time': local.strftime('%H:%M'),
            'can_cancel': visit.state in ('draft', 'confirmed') and visit.start > fields.Datetime.now(),
            'cancelled': kw.get('cancelled'),
        })
        return request.render('medical_website.booking_done', values)

    @http.route('/appointment/booking/<string:token>/cancel', type='http', auth='public', website=True,
                methods=['POST'], sitemap=False)
    def appointment_cancel(self, token, **post):
        try:
            self._engine().cancel(token)
        except UserError:
            pass
        return request.redirect(f'/appointment/booking/{token}?cancelled=1')
