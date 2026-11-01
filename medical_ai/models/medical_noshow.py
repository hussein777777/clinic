import math
from collections import defaultdict
from datetime import timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

OUTCOME_STATES = ('done', 'invoiced', 'no_show')
PATIENT_PRIOR = 4.0   # pseudo-visits pulling a patient's own rate toward the clinic rate
SLOT_PRIOR = 20.0     # pseudo-visits for weekday x hour rates
DEFAULT_BASE = 0.10   # used until the clinic has 30 visits with a known outcome
MULTIPLIERS = {
    'first_visit': 1.3,
    'lead_long': 1.4,     # booked more than 14 days ahead
    'lead_short': 0.6,    # booked for today / tomorrow
    'overdue': 1.4,       # patient has an overdue balance
    'confirmed': 0.6,     # patient confirmed the booking
}


def _logit(p):
    p = min(max(p, 0.001), 0.999)
    return math.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


class MedicalVisit(models.Model):
    _inherit = 'medical.visit'

    noshow_risk = fields.Integer(string='No-show Risk (%)', readonly=True, copy=False,
                                 help='Estimated chance the patient does not come, from this clinic\'s own history.')
    noshow_level = fields.Selection([('low', 'Low'), ('medium', 'Medium'), ('high', 'High')],
                                    string='No-show Risk', readonly=True, copy=False, index=True)
    noshow_factors = fields.Text(string='Why', readonly=True, copy=False)

    # ----------------------------------------------------------------- stats
    @api.model
    def _local_slot(self, start, tz):
        local = pytz.utc.localize(start).astimezone(tz)
        return local.weekday(), local.hour

    @api.model
    def _noshow_history(self, before=None):
        """Clinic base rate and smoothed weekday x hour multipliers over the last 12 months."""
        before = before or fields.Datetime.now()
        domain = [('state', 'in', OUTCOME_STATES), ('start', '<', before),
                  ('start', '>=', before - relativedelta(months=12)), ('company_id', '=', self.env.company.id)]
        visits = self.sudo().search_read(domain, ['start', 'state'], limit=20000, order='start desc')
        total = len(visits)
        missed = sum(1 for v in visits if v['state'] == 'no_show')
        base = missed / total if total >= 30 else DEFAULT_BASE
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        slot_total, slot_missed = defaultdict(int), defaultdict(int)
        for v in visits:
            slot = self._local_slot(v['start'], tz)
            slot_total[slot] += 1
            if v['state'] == 'no_show':
                slot_missed[slot] += 1
        slots = {}
        for slot, n in slot_total.items():
            rate = (slot_missed[slot] + SLOT_PRIOR * base) / (n + SLOT_PRIOR)
            slots[slot] = min(max(rate / base, 0.5), 2.0) if base else 1.0
        return {'base': base, 'slots': slots, 'tz': tz, 'visits': total}

    @api.model
    def _score(self, base, patient_n, patient_k, slot_mult=1.0, lead_days=None, overdue=False, confirmed=False):
        """Return (probability, [(log-odds effect, explanation)])."""
        effects = []
        patient_rate = (patient_k + PATIENT_PRIOR * base) / (patient_n + PATIENT_PRIOR)
        logit = _logit(patient_rate)
        if patient_n:
            effects.append((_logit(patient_rate) - _logit(base),
                            _("Missed %(k)s of %(n)s past visits", k=patient_k, n=patient_n)))
        else:
            logit += math.log(MULTIPLIERS['first_visit'])
            effects.append((math.log(MULTIPLIERS['first_visit']), _("First visit")))
        if abs(math.log(slot_mult)) > 0.05:
            logit += math.log(slot_mult)
            effects.append((math.log(slot_mult), _("This day and hour have more no-shows") if slot_mult > 1
                            else _("This day and hour are usually kept")))
        if lead_days is not None:
            if lead_days > 14:
                logit += math.log(MULTIPLIERS['lead_long'])
                effects.append((math.log(MULTIPLIERS['lead_long']), _("Booked %s days ahead", lead_days)))
            elif lead_days <= 1:
                logit += math.log(MULTIPLIERS['lead_short'])
                effects.append((math.log(MULTIPLIERS['lead_short']), _("Booked for today or tomorrow")))
        if overdue:
            logit += math.log(MULTIPLIERS['overdue'])
            effects.append((math.log(MULTIPLIERS['overdue']), _("Has an overdue balance")))
        if confirmed:
            logit += math.log(MULTIPLIERS['confirmed'])
            effects.append((math.log(MULTIPLIERS['confirmed']), _("Confirmed the booking")))
        return _sigmoid(logit), effects

    @api.model
    def _level(self, probability, base):
        if probability >= max(0.30, 2.5 * base):
            return 'high'
        if probability >= max(0.15, 1.5 * base):
            return 'medium'
        return 'low'

    def _compute_noshow(self):
        visits = self.filtered(lambda v: v.state in ('draft', 'confirmed', 'checked_in') and v.start and v.patient_id)
        if not visits:
            return
        history = self._noshow_history()
        base = history['base']
        stats = defaultdict(lambda: [0, 0])
        for patient, state, count in self.sudo()._read_group(
                [('patient_id', 'in', visits.patient_id.ids), ('state', 'in', OUTCOME_STATES)],
                ['patient_id', 'state'], ['__count']):
            stats[patient.id][0] += count
            if state == 'no_show':
                stats[patient.id][1] += count
        today = fields.Date.context_today(self)
        overdue = {partner.id for partner, _count in self.env['account.move.line'].sudo()._read_group([
            ('partner_id', 'in', visits.patient_id.ids),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('reconciled', '=', False),
            ('date_maturity', '<', today),
            ('amount_residual', '>', 0),
        ], ['partner_id'], ['__count'])}
        for visit in visits:
            n, k = stats[visit.patient_id.id]
            slot = self._local_slot(visit.start, history['tz'])
            created = visit.create_date or fields.Datetime.now()
            lead_days = max((visit.start - created).days, 0)
            probability, effects = self._score(
                base, n, k,
                slot_mult=history['slots'].get(slot, 1.0),
                lead_days=lead_days,
                overdue=visit.patient_id.id in overdue,
                confirmed=visit.state == 'confirmed',
            )
            effects.sort(key=lambda e: -abs(e[0]))
            factors = '\n'.join(('↑ ' if effect > 0 else '↓ ') + text for effect, text in effects if abs(effect) > 0.05)
            visit.with_context(medical_skip_noshow=True).write({
                'noshow_risk': round(probability * 100),
                'noshow_level': self._level(probability, base),
                'noshow_factors': factors or False,
            })

    @api.model_create_multi
    def create(self, vals_list):
        visits = super().create(vals_list)
        visits._compute_noshow()
        return visits

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('medical_skip_noshow') and {'start', 'patient_id', 'state'} & set(vals):
            self._compute_noshow()
        return res

    @api.model
    def _cron_refresh_noshow_risk(self):
        now = fields.Datetime.now()
        lang = self.env['medical.ai.service']._clinic_lang()
        self.with_context(lang=lang).search([('state', 'in', ('draft', 'confirmed')), ('start', '>=', now),
                     ('start', '<', now + timedelta(days=14))])._compute_noshow()

    # -------------------------------------------------------------- backtest
    @api.model
    def _noshow_backtest(self, months=3):
        """Score past visits with only what was known before each one and compare with what happened."""
        now = fields.Datetime.now()
        start = now - relativedelta(months=months)
        history = self._noshow_history(before=start)
        base = history['base']
        visits = self.sudo().search_read(
            [('state', 'in', OUTCOME_STATES), ('start', '<', now), ('company_id', '=', self.env.company.id)],
            ['start', 'state', 'patient_id', 'create_date'], order='start asc', limit=50000)
        running = defaultdict(lambda: [0, 0])
        scored = []
        for v in visits:
            patient = v['patient_id'] and v['patient_id'][0]
            n, k = running[patient]
            if v['start'] >= start:
                slot = self._local_slot(v['start'], history['tz'])
                lead_days = max((v['start'] - v['create_date']).days, 0) if v['create_date'] else None
                probability, _effects = self._score(base, n, k, history['slots'].get(slot, 1.0), lead_days)
                scored.append((probability, v['state'] == 'no_show'))
            running[patient][0] += 1
            if v['state'] == 'no_show':
                running[patient][1] += 1
        if not scored:
            return {'visits': 0}
        scored.sort(key=lambda s: -s[0])
        total_missed = sum(1 for _p, missed in scored if missed)
        top = scored[:max(1, len(scored) // 5)]
        top_missed = sum(1 for _p, missed in top if missed)
        high = [s for s in scored if self._level(s[0], base) == 'high']
        return {
            'visits': len(scored),
            'no_show_rate': round(100.0 * total_missed / len(scored), 1),
            'top20_rate': round(100.0 * top_missed / len(top), 1),
            'top20_capture': round(100.0 * top_missed / total_missed, 1) if total_missed else 0.0,
            'high_count': len(high),
            'high_rate': round(100.0 * sum(1 for _p, m in high if m) / len(high), 1) if high else 0.0,
        }

    @api.model
    def action_noshow_backtest(self):
        result = self._noshow_backtest()
        if not result.get('visits'):
            message = _("Not enough visits in the last 3 months to evaluate the model.")
            kind = 'warning'
        else:
            message = _(
                "Last 3 months: %(visits)s visits, %(rate)s%% no-shows. "
                "The 20%% of visits the model flagged as riskiest had %(top)s%% no-shows "
                "and contained %(capture)s%% of all no-shows.",
                visits=result['visits'], rate=result['no_show_rate'],
                top=result['top20_rate'], capture=result['top20_capture'])
            kind = 'success'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'type': kind, 'title': _("No-show model check"), 'message': message, 'sticky': True},
        }
