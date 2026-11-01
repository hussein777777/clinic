from collections import defaultdict
from datetime import date, datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

PERIODS = ('this_month', 'last_month', 'last_90', 'this_year')
# Arab working week display order: Saturday first (python weekday numbers)
WEEK_ORDER = [5, 6, 0, 1, 2, 3, 4]


class MedicalDashboard(models.AbstractModel):
    _name = 'medical.dashboard'
    _description = 'Medical management dashboard data'

    # ------------------------------------------------------------------ periods
    @api.model
    def _period_bounds(self, period, today=None):
        today = today or fields.Date.context_today(self)
        if period == 'last_month':
            date_to = today.replace(day=1) - timedelta(days=1)
            date_from = date_to.replace(day=1)
        elif period == 'last_90':
            date_from, date_to = today - timedelta(days=89), today
        elif period == 'this_year':
            date_from, date_to = today.replace(month=1, day=1), today
        else:
            date_from, date_to = today.replace(day=1), today
        length = (date_to - date_from).days + 1
        prev_to = date_from - timedelta(days=1)
        if period in ('this_month', 'last_month'):
            prev_from = prev_to.replace(day=1)
        elif period == 'this_year':
            prev_from = date_from - relativedelta(years=1)
            prev_to = date_to - relativedelta(years=1)
        else:
            prev_from = prev_to - timedelta(days=length - 1)
        return date_from, date_to, prev_from, prev_to

    def _tz(self):
        return pytz.timezone(self.env.user.tz or self.env.context.get('tz') or 'UTC')

    def _utc_range(self, date_from, date_to):
        tz = self._tz()
        start = tz.localize(datetime.combine(date_from, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        stop = tz.localize(datetime.combine(date_to + timedelta(days=1), time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        return start, stop

    # -------------------------------------------------------------- production
    def _production_domain(self, date_from, date_to, doctor_id, department_id=False):
        domain = [
            ('state', '=', 'executed'),
            ('date_executed', '>=', date_from),
            ('date_executed', '<=', date_to),
            ('company_id', '=', self.env.company.id),
        ]
        if doctor_id:
            domain.append(('doctor_id', '=', doctor_id))
        if department_id:
            domain.append(('department_id', '=', department_id))
        return domain

    def _program_recognized(self, date_from, date_to, doctor_id, groupby=None, department_id=False):
        """Care program revenue earned in the period (months served, sessions done)."""
        company = self.env.company
        domain = [
            ('move_id.medical_move_kind', '=', 'program_recognition'),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', 'in', ('income', 'income_other')),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('company_id', '=', company.id),
        ]
        if doctor_id:
            domain.append(('medical_doctor_id', '=', doctor_id))
        if department_id:
            domain.append(('move_id.care_contract_id.department_id', '=', department_id))
        if not groupby:
            data = self.env['account.move.line']._read_group(domain, [], ['balance:sum'])
            return -(data[0][0] or 0.0)
        data = self.env['account.move.line']._read_group(domain, [groupby], ['balance:sum'])
        return {key: -(amount or 0.0) for key, amount in data}

    def _production_total(self, date_from, date_to, doctor_id, department_id=False):
        Line = self.env['medical.treatment.line']
        data = Line._read_group(self._production_domain(date_from, date_to, doctor_id, department_id), [],
                                ['patient_amount:sum', 'insurance_amount:sum'])
        patient, insurer = data[0]
        programs = self._program_recognized(date_from, date_to, doctor_id, department_id=department_id)
        return {
            'patient': patient or 0.0,
            'insurer': insurer or 0.0,
            'programs': programs,
            'total': (patient or 0.0) + (insurer or 0.0) + programs,
        }

    def _collections_total(self, date_from, date_to, doctor_id, department_id=False):
        collected = self.env['medical.commission.line']._collected_by_line(self.env.company, date_from, date_to)
        total = 0.0
        for line, (amount, _date) in collected.items():
            if doctor_id and line.medical_doctor_id.id != doctor_id:
                continue
            if department_id:
                dept = line.medical_line_id.department_id or line.move_id.care_contract_id.department_id \
                    or line.move_id.medical_visit_id.department_id
                if dept.id != department_id:
                    continue
            total += amount
        return total

    def _visit_stats(self, date_from, date_to, doctor_id, department_id=False):
        start, stop = self._utc_range(date_from, date_to)
        domain = [('start', '>=', start), ('start', '<', stop), ('company_id', '=', self.env.company.id)]
        if doctor_id:
            domain.append(('doctor_id', '=', doctor_id))
        if department_id:
            domain.append(('department_id', '=', department_id))
        counts = dict(self.env['medical.visit']._read_group(domain, ['state'], ['__count']))
        done = counts.get('done', 0) + counts.get('invoiced', 0)
        no_show = counts.get('no_show', 0)
        attended_or_missed = done + no_show
        return {
            'done': done,
            'no_show': no_show,
            'no_show_rate': 100.0 * no_show / attended_or_missed if attended_or_missed else 0.0,
        }

    def _new_patients(self, date_from, date_to, department_id=False):
        start, stop = self._utc_range(date_from, date_to)
        if department_id:
            # first visit ever in this clinic during the period
            Visit = self.env['medical.visit']
            firsts = Visit._read_group([('department_id', '=', department_id), ('state', 'in', ('done', 'invoiced'))],
                                       ['patient_id'], ['start:min'])
            return sum(1 for _p, first in firsts if first and start <= first < stop)
        return self.env['res.partner'].search_count([
            ('is_patient', '=', True), ('create_date', '>=', start), ('create_date', '<', stop)])

    # --------------------------------------------------------------- occupancy
    def _opening(self):
        params = self.env['ir.config_parameter'].sudo()
        open_hour = int(params.get_param('medical_dashboard.open_hour', 10))
        close_hour = int(params.get_param('medical_dashboard.close_hour', 22))
        closed = {int(d) for d in (params.get_param('medical_dashboard.closed_weekdays', '4') or '').split(',')
                  if d.strip().isdigit()}
        return open_hour, max(close_hour, open_hour + 1), closed

    def _occupancy(self, date_from, date_to, department_id=False):
        open_hour, close_hour, closed = self._opening()
        hours = list(range(open_hour, close_hour))
        room_domain = [('company_id', '=', self.env.company.id)]
        if department_id:
            room_domain.append(('department_id', '=', department_id))
        room_records = self.env['medical.room'].search(room_domain)
        rooms = len(room_records)
        day_count = defaultdict(int)
        day = date_from
        while day <= date_to:
            day_count[day.weekday()] += 1
            day += timedelta(days=1)
        start, stop = self._utc_range(date_from, date_to)
        visits = self.env['medical.visit'].search([
            ('start', '>=', start), ('start', '<', stop), ('room_id', 'in', room_records.ids),
            ('state', 'not in', ('cancelled', 'no_show')), ('company_id', '=', self.env.company.id)])
        tz = self._tz()
        busy = defaultdict(float)  # (weekday, hour) -> room-hours
        for visit in visits:
            local_start = pytz.utc.localize(visit.start).astimezone(tz).replace(tzinfo=None)
            local_stop = local_start + timedelta(hours=visit.duration or 0.5)
            cursor = local_start.replace(minute=0, second=0, microsecond=0)
            while cursor < local_stop:
                slot_end = cursor + timedelta(hours=1)
                overlap = (min(slot_end, local_stop) - max(cursor, local_start)).total_seconds() / 3600.0
                if overlap > 0:
                    busy[(cursor.weekday(), cursor.hour)] += overlap
                cursor = slot_end
        cells, busy_open, capacity_open = [], 0.0, 0.0
        for weekday in WEEK_ORDER:
            for hour in hours:
                capacity = rooms * day_count[weekday]
                used = busy[(weekday, hour)]
                is_closed = weekday in closed
                if not is_closed:
                    busy_open += used
                    capacity_open += capacity
                cells.append({
                    'weekday': weekday,
                    'hour': hour,
                    'pct': round(100.0 * used / capacity, 1) if capacity and not is_closed else None,
                    'hours': round(used, 1),
                })
        return {
            'hours': hours,
            'weekdays': WEEK_ORDER,
            'cells': cells,
            'rooms': rooms,
            'utilization': round(100.0 * busy_open / capacity_open, 1) if capacity_open else 0.0,
        }

    # --------------------------------------------------------------- insurance
    def _insurance(self, date_from, date_to, department_id=False):
        ClaimLine = self.env['medical.claim.line']
        domain = [('service_date', '>=', date_from), ('service_date', '<=', date_to),
                  ('company_id', '=', self.env.company.id)]
        if department_id:
            domain.append(('department_id', '=', department_id))
        settled_states = ('approved', 'partially_approved', 'rejected', 'rebilled', 'written_off', 'resubmitted')
        claimed = dict(ClaimLine._read_group(domain, ['insurer_id'], ['claimed:sum']))
        settled = {ins: (c, r) for ins, c, r in ClaimLine._read_group(
            domain + [('state', 'in', settled_states)], ['insurer_id'], ['claimed:sum', 'rejected_amount:sum'])}
        rows = []
        for insurer, amount in claimed.items():
            settled_claimed, rejected = settled.get(insurer, (0.0, 0.0))
            rows.append({
                'id': insurer.id,
                'name': insurer.display_name,
                'claimed': amount or 0.0,
                'settled': settled_claimed or 0.0,
                'rejected': rejected or 0.0,
                'rate': round(100.0 * rejected / settled_claimed, 1) if settled_claimed else 0.0,
            })
        rows.sort(key=lambda r: (-r['rate'], -r['rejected']))
        total_settled = sum(r['settled'] for r in rows)
        total_rejected = sum(r['rejected'] for r in rows)
        reasons = ClaimLine._read_group(domain + [('rejected_amount', '>', 0)], ['reject_reason'],
                                        ['__count', 'rejected_amount:sum'], order='rejected_amount:sum desc', limit=6)
        return {
            'insurers': rows[:8],
            'rejection_rate': round(100.0 * total_rejected / total_settled, 1) if total_settled else 0.0,
            'rejected': total_rejected,
            'reasons': [{'reason': reason or _("No reason given"), 'count': count, 'amount': amount or 0.0}
                        for reason, count, amount in reasons],
        }

    # -------------------------------------------------------------- breakdowns
    def _by_doctor(self, date_from, date_to, doctor_id, department_id=False):
        Line = self.env['medical.treatment.line']
        data = Line._read_group(self._production_domain(date_from, date_to, doctor_id, department_id), ['doctor_id'],
                                ['price_subtotal:sum'])
        totals = defaultdict(float)
        names = {}
        for doctor, amount in data:
            totals[doctor.id] += amount or 0.0
            names[doctor.id] = doctor.sudo().name if doctor else _("Unassigned")
        for doctor, amount in self._program_recognized(date_from, date_to, doctor_id, 'medical_doctor_id',
                                                       department_id=department_id).items():
            totals[doctor.id] += amount
            names[doctor.id] = doctor.sudo().name if doctor else _("Unassigned")
        rows = [{'id': key, 'name': names[key], 'amount': amount} for key, amount in totals.items() if amount]
        return sorted(rows, key=lambda r: -r['amount'])[:10]

    def _by_department(self, date_from, date_to, doctor_id):
        """Production per clinic (all clinics view)."""
        Line = self.env['medical.treatment.line']
        data = Line._read_group(self._production_domain(date_from, date_to, doctor_id), ['department_id'],
                                ['price_subtotal:sum'])
        totals, names = defaultdict(float), {}
        for department, amount in data:
            totals[department.id] += amount or 0.0
            names[department.id] = department.name if department else _("Other")
        for move, amount in self._program_recognized(date_from, date_to, doctor_id, 'move_id').items():
            department = move.care_contract_id.department_id
            totals[department.id] += amount
            names[department.id] = department.name if department else _("Other")
        rows = [{'key': key, 'name': names[key], 'amount': amount} for key, amount in totals.items() if amount]
        return sorted(rows, key=lambda r: -r['amount'])

    def _by_specialty(self, date_from, date_to, doctor_id, department_id=False):
        """Production per specialty inside one clinic."""
        Line = self.env['medical.treatment.line']
        data = Line._read_group(self._production_domain(date_from, date_to, doctor_id, department_id), ['specialty_id'],
                                ['price_subtotal:sum'])
        totals, names = defaultdict(float), {}
        for specialty, amount in data:
            totals[specialty.id] += amount or 0.0
            names[specialty.id] = specialty.name if specialty else _("Other")
        programs = self._program_recognized(date_from, date_to, doctor_id, department_id=department_id)
        if programs:
            totals['programs'] += programs
            names['programs'] = _("Care programs")
        rows = [{'key': key, 'name': names[key], 'amount': amount} for key, amount in totals.items() if amount]
        return sorted(rows, key=lambda r: -r['amount'])

    def _trend(self, today, doctor_id, department_id=False):
        first = (today.replace(day=1) - relativedelta(months=11))
        Line = self.env['medical.treatment.line']
        data = Line._read_group(self._production_domain(first, today, doctor_id, department_id), ['date_executed:month'],
                                ['patient_amount:sum', 'insurance_amount:sum'])
        by_month = {month.strftime('%Y-%m'): (patient or 0.0, insurer or 0.0) for month, patient, insurer in data}
        programs = {month.strftime('%Y-%m'): amount for month, amount in self._program_recognized(
            first, today, doctor_id, 'date:month', department_id=department_id).items()}
        rows = []
        for i in range(12):
            month = first + relativedelta(months=i)
            key = month.strftime('%Y-%m')
            patient, insurer = by_month.get(key, (0.0, 0.0))
            rows.append({
                'key': key,
                'label': self._month_label(month),
                'patient': patient,
                'insurer': insurer,
                'programs': programs.get(key, 0.0),
            })
        return rows

    def _date_label(self, day):
        from babel.dates import format_date
        locale = (self.env.user.lang or 'en_US').replace('-', '_')
        try:
            return format_date(day, 'd MMM y', locale=locale)
        except Exception:  # noqa: BLE001
            return day.strftime('%d %b %Y')

    def _month_label(self, month):
        from babel.dates import format_date
        locale = (self.env.user.lang or 'en_US').replace('-', '_')
        try:
            return format_date(month, 'MMM y', locale=locale)
        except Exception:  # noqa: BLE001 - unknown locale: fall back to english
            return month.strftime('%b %Y')

    # ---------------------------------------------------------------- leakage
    def _leakage(self, doctor_id, department_id=False):
        company = self.env.company
        today = fields.Date.context_today(self)
        items = []

        def add(key, label, hint, amount, count, model, domain, name):
            items.append({'key': key, 'label': label, 'hint': hint, 'amount': amount, 'count': count,
                          'action': {'res_model': model, 'domain': domain, 'name': name}})

        line_domain = [('state', '=', 'planned'), ('visit_id', '=', False),
                       ('plan_id.state', 'in', ('accepted', 'in_progress')), ('company_id', '=', company.id)]
        if doctor_id:
            line_domain.append(('doctor_id', '=', doctor_id))
        if department_id:
            line_domain.append(('department_id', '=', department_id))
        amount, count = self.env['medical.treatment.line']._read_group(line_domain, [], ['price_subtotal:sum', '__count'])[0]
        add('unscheduled', _("Accepted treatments not booked"), _("Call these patients to book"),
            amount or 0.0, count, 'medical.treatment.line', line_domain, _("Accepted treatments not booked"))

        overdue_domain = [
            ('move_id.medical_move_kind', 'in', ('patient', 'advance', 'rebill', 'program_contract')),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('reconciled', '=', False),
            ('date_maturity', '<', today),
            ('company_id', '=', company.id),
        ]
        amount, count = self.env['account.move.line']._read_group(overdue_domain, [], ['amount_residual:sum', '__count'])[0]
        add('overdue', _("Overdue patient balances"), _("Invoices and installments past due"),
            amount or 0.0, count, 'account.move.line', overdue_domain, _("Overdue patient balances"))

        follow_domain = [('follow_up_date', '!=', False), ('follow_up_date', '<=', today + timedelta(days=7)),
                         ('follow_up_visit_id', '=', False), ('state', 'in', ('done', 'invoiced')),
                         ('company_id', '=', company.id)]
        if doctor_id:
            follow_domain.append(('doctor_id', '=', doctor_id))
        if department_id:
            follow_domain.append(('department_id', '=', department_id))
        count = self.env['medical.visit'].search_count(follow_domain)
        add('follow_up', _("Follow-ups not booked"), _("Due within a week or already late"),
            None, count, 'medical.visit', follow_domain, _("Follow-ups to Book"))

        claim_domain = [('state', '=', 'to_claim'), ('company_id', '=', company.id)]
        if department_id:
            claim_domain.append(('department_id', '=', department_id))
        amount, count = self.env['medical.claim.line']._read_group(claim_domain, [], ['claimed:sum', '__count'])[0]
        add('to_claim', _("Insurer shares not yet claimed"), _("Create the monthly claims"),
            amount or 0.0, count, 'medical.claim.line', claim_domain, _("Claim Lines"))

        reject_domain = [('state', 'in', ('rejected', 'partially_approved')), ('rejected_amount', '>', 0),
                         ('company_id', '=', company.id)]
        if department_id:
            reject_domain.append(('department_id', '=', department_id))
        amount, count = self.env['medical.claim.line']._read_group(reject_domain, [], ['rejected_amount:sum', '__count'])[0]
        add('rejections', _("Rejections waiting for a decision"), _("Re-bill, write off or resubmit"),
            amount or 0.0, count, 'medical.claim.line', reject_domain, _("Rejections to Decide"))

        results_domain = [('state', 'in', ('requested', 'in_progress')), ('company_id', '=', company.id),
                          ('date', '<', fields.Datetime.now() - timedelta(days=2))]
        if department_id:
            results_domain.append(('department_id', '=', department_id))
        count = self.env['medical.order'].search_count(results_domain)
        add('pending_results', _("Tests waiting for results"), _("Requested more than 2 days ago"),
            None, count, 'medical.order', results_domain, _("Lab & Imaging"))
        task_domain = ['|', ('res_model', '=like', 'medical.%'), ('res_model', '=like', 'dental.%'),
                       ('date_deadline', '<', today)]
        count = self.env['mail.activity'].sudo().search_count(task_domain)
        add('overdue_tasks', _("Overdue clinical tasks"), _("Cast removals, recalls, vaccinations, result reviews..."),
            None, count, 'mail.activity', task_domain, _("Overdue Clinical Tasks"))
        self._leakage_extra(add, doctor_id, department_id)
        return items

    def _leakage_extra(self, add, doctor_id, department_id):
        """Hook for specialty modules (late dental lab work, overdue cast removals...)."""
        return

    # -------------------------------------------------------------------- API
    @api.model
    def get_dashboard_data(self, period='this_month', doctor_id=False, department_id=False):
        period = period if period in PERIODS else 'this_month'
        doctor_id = int(doctor_id) if doctor_id else False
        department_id = int(department_id) if department_id else False
        today = fields.Date.context_today(self)
        date_from, date_to, prev_from, prev_to = self._period_bounds(period, today)
        currency = self.env.company.currency_id

        dep = department_id
        production = self._production_total(date_from, date_to, doctor_id, dep)
        production_prev = self._production_total(prev_from, prev_to, doctor_id, dep)
        collected = self._collections_total(date_from, date_to, doctor_id, dep)
        collected_prev = self._collections_total(prev_from, prev_to, doctor_id, dep)
        visits = self._visit_stats(date_from, date_to, doctor_id, dep)
        visits_prev = self._visit_stats(prev_from, prev_to, doctor_id, dep)
        occupancy = self._occupancy(date_from, date_to, dep)
        insurance = self._insurance(date_from, date_to, dep)

        doctor_domain = [('is_practitioner', '=', True), ('company_id', 'in', (False, self.env.company.id))]
        if dep:
            doctor_domain.append(('medical_department_ids', 'in', dep))
        doctors = self.env['hr.employee'].sudo().search(doctor_domain, order='name')
        departments = self.env['medical.department'].search([('company_id', 'in', (False, self.env.company.id))])
        return {
            'period': {
                'key': period,
                'date_from': fields.Date.to_string(date_from),
                'date_to': fields.Date.to_string(date_to),
                'prev_from': fields.Date.to_string(prev_from),
                'prev_to': fields.Date.to_string(prev_to),
                'label': f"{self._date_label(date_from)} – {self._date_label(date_to)}",
                'prev_label': f"{self._date_label(prev_from)} – {self._date_label(prev_to)}",
            },
            'currency': {'symbol': currency.symbol, 'position': currency.position, 'digits': currency.decimal_places},
            'doctors': [{'id': d.id, 'name': d.name} for d in doctors],
            'departments': [{'id': d.id, 'name': d.name} for d in departments],
            'department_id': dep,
            'kpis': {
                'production': production['total'],
                'production_prev': production_prev['total'],
                'insurer_share': production['insurer'],
                'collected': collected,
                'collected_prev': collected_prev,
                'visits': visits['done'],
                'visits_prev': visits_prev['done'],
                'no_show_rate': visits['no_show_rate'],
                'no_show_rate_prev': visits_prev['no_show_rate'],
                'new_patients': self._new_patients(date_from, date_to, dep),
                'utilization': occupancy['utilization'],
                'rejection_rate': insurance['rejection_rate'],
            },
            'trend': self._trend(today, doctor_id, dep),
            'by_doctor': self._by_doctor(date_from, date_to, doctor_id, dep),
            'by_specialty': self._by_specialty(date_from, date_to, doctor_id, dep) if dep
            else self._by_department(date_from, date_to, doctor_id),
            'breakdown': 'specialty' if dep else 'department',
            'occupancy': occupancy,
            'insurance': insurance,
            'leakage': self._leakage(doctor_id, dep),
        }
