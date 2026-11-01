import json
from datetime import timedelta
from typing import Literal

from odoo import _, api, fields, models

from .medical_ai_kb import CATEGORIES

try:
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover
    BaseModel = object

    def Field(*args, **kwargs):  # noqa: N802
        return None

SEVERITIES = ('info', 'warning', 'critical')
INSIGHT_PERIODS = ('this_month', 'last_month', 'last_90', 'this_year')


class Insight(BaseModel):
    title: str = Field(description='Short headline with the key number, e.g. "No-shows up to 9.4%".')
    detail: str = Field(description='One or two sentences explaining the number and its comparison.')
    severity: Literal['info', 'warning', 'critical']
    action: str = Field(description='One concrete next step for the manager.')


class InsightList(BaseModel):
    insights: list[Insight] = Field(max_length=6, description='Most important first.')


class MedicalAiInsight(models.Model):
    _name = 'medical.ai.insight'
    _description = 'AI insights on the center figures'
    _order = 'id desc'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    period = fields.Char(required=True)
    period_label = fields.Char()
    mode = fields.Selection([('rules', 'Rules'), ('ai', 'AI')], required=True)
    items = fields.Json()
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)


class MedicalAiSmartDashboard(models.AbstractModel):
    _name = 'medical.ai.smart.dashboard'
    _description = 'Smart AI dashboard'

    # ------------------------------------------------------------------- usage
    @api.model
    def _usage(self, date_from, date_to):
        Log = self.env['medical.ai.log'].sudo()
        domain = [('create_date', '>=', date_from), ('create_date', '<', date_to)]
        rows = Log._read_group(domain, ['state'], ['__count', 'input_tokens:sum', 'output_tokens:sum', 'cost:sum',
                                                   'duration:avg'])
        total = sum(r[1] for r in rows)
        ok = sum(r[1] for r in rows if r[0] == 'ok')
        return {
            'requests': total,
            'success_rate': round(100.0 * ok / total, 1) if total else None,
            'input_tokens': sum(r[2] or 0 for r in rows),
            'output_tokens': sum(r[3] or 0 for r in rows),
            'cost': round(sum(r[4] or 0.0 for r in rows), 4),
            'avg_duration': round(sum((r[5] or 0) * r[1] for r in rows) / total, 2) if total else None,
            'questions': Log.search_count(domain + [('feature', 'in', ('agent', 'rag'))]),
        }

    @api.model
    def get_data(self, days=30):
        days = int(days) if int(days or 0) in (7, 30, 90) else 30
        now = fields.Datetime.now()
        start = now - timedelta(days=days)
        prev_start = start - timedelta(days=days)
        Log = self.env['medical.ai.log'].sudo()
        service = self.env['medical.ai.service']
        cfg = service._get_config()
        features = dict(Log._fields['feature']._description_selection(self.env))

        by_feature = {}
        for feature, state, count in Log._read_group([('create_date', '>=', start)], ['feature', 'state'], ['__count']):
            by_feature.setdefault(feature, {'ok': 0, 'error': 0})[state] = count
        feature_rows = sorted(by_feature.items(), key=lambda item: -(item[1]['ok'] + item[1]['error']))

        daily = {}
        for day, state, count in Log._read_group([('create_date', '>=', start)], ['create_date:day', 'state'],
                                                 ['__count']):
            daily.setdefault(fields.Date.to_string(day), {'ok': 0, 'error': 0})[state] = count
        day_labels, ok_values, error_values = [], [], []
        today = fields.Date.context_today(self)
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            key = fields.Date.to_string(day)
            day_labels.append(self._short_day(day))
            ok_values.append(daily.get(key, {}).get('ok', 0))
            error_values.append(daily.get(key, {}).get('error', 0))

        Doc = self.env['medical.ai.kb.document']
        docs = Doc.search([])
        categories = dict(Doc._fields['category']._description_selection(self.env))
        by_category = {}
        for doc in docs.filtered(lambda d: d.state == 'indexed'):
            by_category[doc.category] = by_category.get(doc.category, 0) + 1
        _emb, signature = service._lc_embeddings(cfg)
        insight = self.env['medical.ai.insight'].search([('company_id', '=', self.env.company.id)], limit=1)
        sessions = self.env['medical.ai.chat.session'].search([('user_id', '=', self.env.user.id)], limit=1)
        return {
            'days': days,
            'engine': {
                'engine': cfg['engine'],
                'provider': dict(self.env['res.config.settings']._fields['medical_ai_provider']
                                 ._description_selection(self.env)).get(cfg['provider']),
                'model': cfg['model'],
                'configured': service._is_configured(),
                'embedding': signature,
                'vector_store': 'pgvector' if self.env['medical.ai.kb.chunk']._use_pgvector() else 'table',
                'anonymize': cfg['anonymize'],
                'is_manager': self.env.user.has_group('medical_core.group_medical_manager'),
            },
            'usage': self._usage(start, now),
            'usage_prev': self._usage(prev_start, start),
            'by_feature': {
                'labels': [features.get(key, key) for key, _counts in feature_rows],
                'ok': [counts['ok'] for _key, counts in feature_rows],
                'error': [counts['error'] for _key, counts in feature_rows],
            },
            'daily': {'labels': day_labels, 'ok': ok_values, 'error': error_values},
            'kb': {
                'documents': len(docs),
                'indexed': len(docs.filtered(lambda d: d.state == 'indexed')),
                'pending': len(docs.filtered(lambda d: d.state == 'draft')),
                'errors': len(docs.filtered(lambda d: d.state == 'error')),
                'outdated': len(docs.filtered('is_outdated')),
                'passages': sum(docs.mapped('chunk_count')),
                'by_category': [{'label': categories.get(key, key), 'count': count}
                                for key, count in sorted(by_category.items(), key=lambda i: -i[1])],
            },
            'insight': self._insight_dict(insight),
            'session': {
                'id': sessions.id,
                'messages': [m._to_dict() for m in sessions.message_ids.sorted('id')[-12:]],
            } if sessions else {'id': False, 'messages': []},
        }

    @api.model
    def _short_day(self, day):
        from babel.dates import format_date
        try:
            return format_date(day, 'd MMM', locale=(self.env.user.lang or 'en_US').replace('-', '_'))
        except Exception:  # noqa: BLE001 - unknown locale: plain day/month
            return f"{day.day}/{day.month}"

    @api.model
    def _insight_dict(self, insight):
        if not insight:
            return None
        return {'id': insight.id, 'mode': insight.mode, 'period_label': insight.period_label,
                'date': self.env['medical.dashboard']._date_label(fields.Date.context_today(self, insight.create_date)),
                'items': insight.items or []}

    # ---------------------------------------------------------------- insights
    @api.model
    def _rule_insights(self, data):
        """Deterministic insights from the management dashboard figures (always available)."""
        k = data['kpis']
        cur = data['currency']['symbol']
        items = []

        def change(now, before):
            return (now - before) / before * 100 if before else None

        # a period still running is compared with the previous one at the same pace
        period = data['period']
        span = lambda a, b: (fields.Date.to_date(b) - fields.Date.to_date(a)).days + 1  # noqa: E731
        pace = min(1.0, span(period['date_from'], period['date_to']) / span(period['prev_from'], period['prev_to']))
        before = k['production_prev'] * pace
        prod_change = change(k['production'], before)
        if prod_change is not None and abs(prod_change) >= 10:
            items.append({
                'severity': 'info' if prod_change > 0 else 'warning',
                'title': _("Production %(dir)s %(pct)s%%", dir=_("up") if prod_change > 0 else _("down"),
                           pct=f"{abs(prod_change):.0f}"),
                'detail': (_("%(now)s %(cur)s against %(before)s %(cur)s in the previous period.",
                             now=f"{k['production']:,.0f}", before=f"{before:,.0f}", cur=cur) if pace == 1.0 else
                           _("%(now)s %(cur)s so far, against %(before)s %(cur)s at the same point of the previous period.",
                             now=f"{k['production']:,.0f}", before=f"{before:,.0f}", cur=cur)),
                'action': _("Check which clinics drove the change in Production by clinic."),
            })
        if k['production'] and k['collected'] / k['production'] < 0.8:
            items.append({
                'severity': 'warning',
                'title': _("Collections at %s%% of production", f"{100 * k['collected'] / k['production']:.0f}"),
                'detail': _("Collected %(c)s %(cur)s for %(p)s %(cur)s of treatments; the gap includes insurer shares "
                            "not yet paid.", c=f"{k['collected']:,.0f}", p=f"{k['production']:,.0f}", cur=cur),
                'action': _("Follow up overdue patient balances and submit pending claim batches."),
            })
        if k['no_show_rate'] >= 8 or (k['no_show_rate'] - k['no_show_rate_prev'] >= 2):
            items.append({
                'severity': 'critical' if k['no_show_rate'] >= 12 else 'warning',
                'title': _("No-shows at %s%%", f"{k['no_show_rate']:.1f}"),
                'detail': _("Previous period: %s%%.", f"{k['no_show_rate_prev']:.1f}"),
                'action': _("Confirm high-risk bookings the day before (Reception > Bookings to Confirm)."),
            })
        if k['rejection_rate'] and k['rejection_rate'] >= 10:
            worst = max(data['insurance'].get('insurers') or [{}], key=lambda r: r.get('rate', 0) or 0)
            items.append({
                'severity': 'critical' if k['rejection_rate'] >= 20 else 'warning',
                'title': _("Insurers rejected %s%% of the claimed amount", f"{k['rejection_rate']:.1f}"),
                'detail': _("Highest: %s.", worst.get('name') or '-'),
                'action': _("Run the AI review on medium- and high-risk lines before the next batch."),
            })
        if k['utilization'] is not None and k['utilization'] < 40:
            items.append({
                'severity': 'info',
                'title': _("Rooms booked %s%% of opening hours", f"{k['utilization']:.0f}"),
                'detail': _("Idle hours are the cheapest capacity to sell."),
                'action': _("Use the occupancy heatmap to place follow-ups and programs in quiet slots."),
            })
        leak = max((r for r in data['leakage'] if r.get('amount')), key=lambda r: r['amount'], default=None)
        if leak:
            items.append({
                'severity': 'warning',
                'title': _("%(amount)s %(cur)s waiting: %(label)s", amount=f"{leak['amount']:,.0f}", cur=cur,
                           label=leak['label']),
                'detail': _("%s items in this list on the dashboard.", leak['count']),
                'action': leak.get('hint') or _("Open the list from the dashboard."),
            })
        tasks = next((r for r in data['leakage'] if r.get('key') == 'overdue_tasks'), None)
        if tasks and tasks.get('count'):
            items.append({
                'severity': 'warning' if tasks['count'] < 20 else 'critical',
                'title': _("%s clinical tasks overdue", tasks['count']),
                'detail': _("Cast removals, recalls, vaccinations and result reviews past their date."),
                'action': _("Assign the overdue tasks to the clinics today."),
            })
        if data['by_specialty']:
            top = data['by_specialty'][0]
            total = sum(r['amount'] for r in data['by_specialty']) or 1
            items.append({
                'severity': 'info',
                'title': _("%(name)s brings %(pct)s%% of production", name=top['name'],
                           pct=f"{100 * top['amount'] / total:.0f}"),
                'detail': _("%(amount)s %(cur)s in the period.", amount=f"{top['amount']:,.0f}", cur=cur),
                'action': _("Protect its capacity: doctors' hours and room availability."),
            })
        if not items:
            items.append({
                'severity': 'info',
                'title': _("No warning signs in this period"),
                'detail': _("Production, collections, no-shows, rejections and open work are within the usual limits."),
                'action': _("Check again after the next claim batch."),
            })
        order = {'critical': 0, 'warning': 1, 'info': 2}
        return sorted(items, key=lambda i: order[i['severity']])[:6]

    @api.model
    def generate_insights(self, period='this_month'):
        period = period if period in INSIGHT_PERIODS else 'this_month'
        data = self.env['medical.dashboard'].get_dashboard_data(period)
        rules = self._rule_insights(data)
        service = self.env['medical.ai.service']
        mode = 'rules'
        items = rules
        if service._is_configured() and service._lc_enabled():
            facts = {
                'period': data['period']['label'], 'compared_with': data['period']['prev_label'],
                'currency': data['currency']['symbol'], 'kpis': data['kpis'],
                'production_by_clinic': [{'name': r['name'], 'amount': round(r['amount'], 2)} for r in data['by_specialty'][:8]],
                'production_by_doctor': [{'name': r['name'], 'amount': round(r['amount'], 2)} for r in data['by_doctor'][:8]],
                'insurers': data['insurance'].get('insurers'),
                'money_on_the_table': [{'item': r['label'], 'count': r['count'], 'amount': r['amount']} for r in data['leakage']],
                'rule_based_findings': [i['title'] for i in rules],
            }
            system = (
                "You are an operations analyst for a medical center. From the figures given, write up to 6 insights "
                "for the center manager, most important first. Every number you mention must appear in the figures; "
                "compare with the previous period when it helps. Each insight needs one concrete action. Use "
                "severity 'critical' only for problems that cost money or patient safety now. "
                f"Write titles, details and actions in {service._output_language()}."
            )
            result = service._lc_structured('insight', system, json.dumps(facts, ensure_ascii=False, default=str),
                                            InsightList, max_tokens=1200)
            items = [i.model_dump() for i in result.insights] or rules
            mode = 'ai'
        insight = self.env['medical.ai.insight'].create({
            'period': period, 'period_label': data['period']['label'], 'mode': mode, 'items': items,
        })
        return self._insight_dict(insight)

    @api.model
    def index_pending(self):
        self.env['medical.ai.kb.document'].search([('state', 'in', ('draft', 'error'))]).action_index()
        return True
