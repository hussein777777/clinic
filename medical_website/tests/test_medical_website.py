from datetime import date, datetime, timedelta

import pytz

from odoo import fields
from odoo.exceptions import ValidationError, UserError
from odoo.tests import HttpCase, TransactionCase, tagged

TZ = 'Africa/Cairo'


def next_weekday(start, weekday=0, min_days=3):
    """First date with the given weekday (0 = Monday) at least `min_days` after `start`."""
    day = start + timedelta(days=min_days)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day


def utc(day, hour, minute=0):
    local = pytz.timezone(TZ).localize(datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute))
    return local.astimezone(pytz.utc).replace(tzinfo=None)


def setup_clinic(cls):
    env = cls.env
    cls.website = env['website'].get_current_website()
    cls.website.write({'booking_lead_hours': 2, 'booking_days_ahead': 21, 'booking_auto_confirm': False})
    cls.calendar = env['resource.calendar'].create({
        'name': 'Clinic hours', 'tz': TZ, 'company_id': cls.website.company_id.id,
        'attendance_ids': [(0, 0, {'name': f'Day {d}', 'dayofweek': str(d), 'hour_from': 9, 'hour_to': 13,
                                   'day_period': 'morning'}) for d in range(5)]
                          + [(0, 0, {'name': f'Evening {d}', 'dayofweek': str(d), 'hour_from': 14, 'hour_to': 16,
                                     'day_period': 'afternoon'}) for d in range(5)],
    })
    cls.department = env['medical.department'].create({
        'name': 'Dentistry', 'code': 'WDEN', 'website_published': True, 'online_booking': True,
        'booking_duration': 0.5, 'company_id': cls.website.company_id.id,
        'website_intro': 'Fillings, root canals and crowns'})
    cls.hidden_department = env['medical.department'].create({'name': 'Hidden', 'code': 'WHID'})
    room_a = env['medical.room'].create({'name': 'Chair A', 'department_id': cls.department.id})
    room_b = env['medical.room'].create({'name': 'Chair B', 'department_id': cls.department.id})
    vals = {'is_practitioner': True, 'website_published': True, 'website_booking': True,
            'medical_department_ids': [(6, 0, cls.department.ids)], 'resource_calendar_id': cls.calendar.id,
            'tz': TZ, 'company_id': cls.website.company_id.id}
    cls.dr_mona = env['hr.employee'].create(dict(vals, name='Dr. Mona', default_room_id=room_a.id,
                                                 website_headline='Restorative dentist'))
    cls.dr_karim = env['hr.employee'].create(dict(vals, name='Dr. Karim', default_room_id=room_b.id))
    cls.dr_private = env['hr.employee'].create(dict(vals, name='Dr. Private', website_published=False))
    cls.day = next_weekday(fields.Date.today())


@tagged('post_install', '-at_install', 'medical')
class TestMedicalWebsiteBooking(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        setup_clinic(cls)
        cls.engine = cls.env['medical.website.booking']

    def _form(self, start, overrides=None, **kw):
        values = {'department_id': str(self.department.id), 'start': fields.Datetime.to_string(start),
                  'name': 'Sara Ibrahim', 'phone': '+20 100 555 1234', 'consent': '1'}
        values.update(kw)
        values.update(overrides or {})
        return values

    def test_slots_follow_hours_breaks_and_schedule(self):
        slots = self.engine._slots(self.department, doctor=self.dr_mona, day_from=self.day, days=10)
        times = [s['time'] for s in slots[self.day]]
        self.assertEqual(times[:2], ['09:00', '09:30'])
        self.assertIn('12:30', times)
        self.assertNotIn('13:00', times, "the lunch break is not bookable")
        self.assertIn('14:00', times)
        self.assertNotIn('16:00', times)
        self.assertEqual(len(times), 12)  # 4 h + 2 h in 30-minute slots
        self.assertNotIn(self.day + timedelta(days=5), slots, "Saturday: no working hours")
        # a visit in the schedule takes its slot away, whoever booked it
        patient = self.env['res.partner'].create({'name': 'Walk-in', 'is_patient': True})
        self.env['medical.visit'].create({'patient_id': patient.id, 'doctor_id': self.dr_mona.id,
                                          'start': utc(self.day, 10), 'duration': 1.0})
        times = [s['time'] for s in self.engine._slots(self.department, doctor=self.dr_mona, day_from=self.day,
                                                         days=10)[self.day]]
        self.assertNotIn('10:00', times)
        self.assertNotIn('10:30', times)
        self.assertIn('11:00', times)

    def test_lead_time(self):
        now = utc(self.day, 9, 15)
        slots = self.engine._slots(self.department, doctor=self.dr_mona, now=now, days=10)
        self.assertEqual(slots[self.day][0]['time'], '11:30', "at least 2 hours ahead")

    def test_any_doctor_and_double_booking(self):
        start = utc(self.day, 9)
        slots = self.engine._slots(self.department, day_from=self.day, days=10)
        first = slots[self.day][0]
        self.assertEqual(sorted(first['doctor_ids']), sorted((self.dr_mona | self.dr_karim).ids))
        self.assertNotIn(self.dr_private.id, first['doctor_ids'], "unpublished doctors are not bookable")
        v1 = self.engine.book(self._form(start))
        v2 = self.engine.book(self._form(start, name='Omar Ali', phone='+20 111 222 3333'))
        self.assertEqual(v1.doctor_id | v2.doctor_id, self.dr_mona | self.dr_karim)
        with self.assertRaises(ValidationError):
            self.engine.book(self._form(start, name='Third Person', phone='+20 122 999 0000'))
        with self.assertRaises(ValidationError):
            self.engine.book(self._form(start, name='Mona fan', phone='+20 155 000 1111', doctor_id=str(self.dr_mona.id)))

    def test_booking_creates_patient_and_visit(self):
        start = utc(self.day, 11)
        visit = self.engine.book(self._form(start, email='sara@example.com', note='Tooth pain', gender='female',
                                            doctor_id=str(self.dr_karim.id)), remote_addr='10.0.0.1')
        self.assertEqual((visit.state, visit.booking_source, visit.doctor_id), ('draft', 'website', self.dr_karim))
        self.assertEqual(visit.room_id, self.dr_karim.default_room_id)
        self.assertEqual(visit.start, start)
        self.assertEqual(len(visit.booking_token), 32)
        self.assertEqual(visit.visit_type, 'new')
        patient = visit.patient_id
        self.assertTrue(patient.is_patient and patient.patient_code)
        self.assertEqual((patient.name, patient.gender, patient.email), ('Sara Ibrahim', 'female', 'sara@example.com'))
        mail = self.env['mail.mail'].search([('email_to', '=', 'sara@example.com')])
        self.assertTrue(mail, "a confirmation e-mail is queued")
        self.assertIn(visit.booking_token, mail.body_html)
        # the same phone written differently books for the same patient
        other = self.engine.book(self._form(utc(self.day + timedelta(days=1), 9), phone='00201005551234', name='Sara I.'))
        self.assertEqual(other.patient_id, patient)
        self.assertEqual(other.booking_name, 'Sara I.')
        # two upcoming online bookings per phone at most
        with self.assertRaises(ValidationError):
            self.engine.book(self._form(utc(self.day + timedelta(days=2), 9), phone='01005551234'))

    def test_auto_confirm(self):
        self.website.booking_auto_confirm = True
        visit = self.engine.book(self._form(utc(self.day, 14)))
        self.assertEqual(visit.state, 'confirmed')

    def test_form_checks(self):
        start = utc(self.day, 12)
        for bad in ({'website_hp': 'spam'}, {'phone': '123'}, {'name': 'A'}, {'consent': ''},
                    {'email': 'not-an-email'}, {'department_id': str(self.hidden_department.id)},
                    {'start': 'tomorrow'}, {'start': fields.Datetime.to_string(utc(self.day, 13))}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.engine.book(self._form(start, overrides=bad))
        self.assertFalse(self.env['medical.visit'].search([('booking_source', '=', 'website')]))

    def test_cancel_with_token(self):
        visit = self.engine.book(self._form(utc(self.day, 15)))
        self.engine.cancel(visit.booking_token)
        self.assertEqual(visit.state, 'cancelled')
        self.assertTrue(visit.booking_cancelled_online)
        with self.assertRaises(UserError):
            self.engine.cancel(visit.booking_token)
        with self.assertRaises(UserError):
            self.engine.cancel('0' * 32)

    def test_next_slot(self):
        best = self.engine._next_slot(days=14)
        self.assertTrue(best)
        self.assertEqual(best['department'], self.department)
        self.assertIn(best['doctor'], self.dr_mona | self.dr_karim)


@tagged('post_install', '-at_install', 'medical')
class TestMedicalWebsitePages(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        setup_clinic(cls)

    def test_public_pages(self):
        slug = self.env['ir.http']._slug
        for url in ('/clinic', '/clinic/doctors', f'/clinic/department/{slug(self.department)}',
                    f'/clinic/doctor/{slug(self.dr_mona)}', f'/clinic/doctor/{self.dr_mona.id}/photo', '/appointment'):
            response = self.url_open(url)
            self.assertEqual(response.status_code, 200, url)
        home = self.url_open('/clinic').text
        self.assertIn('Dr. Mona', home)
        self.assertNotIn('Dr. Private', home)
        self.assertIn('Next free appointment', home)
        for url in (f'/clinic/doctor/{slug(self.dr_private)}', f'/clinic/doctor/{self.dr_private.id}/photo',
                    f'/clinic/department/{slug(self.hidden_department)}', '/appointment/booking/' + 'f' * 32):
            self.assertEqual(self.url_open(url).status_code, 404, url)

    def test_slots_endpoint(self):
        result = self.make_jsonrpc_request('/appointment/slots', {'department_id': self.department.id})
        self.assertTrue(result['days'])
        self.assertEqual(result['days'][0]['slots'][0]['time'][-2:], '00')
        self.assertEqual(self.make_jsonrpc_request('/appointment/slots', {
            'department_id': self.department.id, 'doctor_id': self.dr_private.id}), {'days': []})

    def test_booking_tour(self):
        self.start_tour('/appointment', 'medical_website_booking_tour')
        visit = self.env['medical.visit'].search([('booking_source', '=', 'website')])
        self.assertEqual(len(visit), 1)
        self.assertEqual(visit.booking_name, 'Laila Hassan')
        self.assertEqual(visit.department_id, self.department)
