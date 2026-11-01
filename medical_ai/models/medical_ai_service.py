import json
import logging
import re
import time

import requests
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.modules import module as odoo_module
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PROVIDERS = [
    ('openai', 'OpenAI'),
    ('anthropic', 'Anthropic (Claude)'),
    ('gemini', 'Google Gemini'),
    ('openai_compatible', 'OpenAI-compatible server (Ollama, vLLM, Azure...)'),
]
PHONE_RE = re.compile(r'\+?\d[\d\s\-]{7,}\d')
TIMEOUT = 60


class MedicalAiLog(models.Model):
    _name = 'medical.ai.log'
    _description = 'Medical AI request log'
    _order = 'id desc'

    feature = fields.Selection([
        ('brief', 'Pre-visit brief'),
        ('followup', 'Follow-up message'),
        ('appeal', 'Appeal letter'),
        ('test', 'Connection test'),
    ], required=True)
    provider = fields.Char()
    model = fields.Char()
    res_model = fields.Char()
    res_id = fields.Integer()
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    duration = fields.Float(string='Duration (s)', digits=(16, 2))
    prompt_chars = fields.Integer()
    response_chars = fields.Integer()
    state = fields.Selection([('ok', 'Success'), ('error', 'Error')], required=True)
    error = fields.Text()
    prompt = fields.Text(help='Only stored when "Keep prompts in the log" is enabled.')


class MedicalAiService(models.AbstractModel):
    _name = 'medical.ai.service'
    _description = 'Medical AI provider gateway'

    # ---------------------------------------------------------------- config
    @api.model
    def _get_config(self):
        params = self.env['ir.config_parameter'].sudo()
        return {
            'provider': params.get_param('medical_ai.provider', 'openai'),
            'model': (params.get_param('medical_ai.model') or '').strip(),
            'api_key': (params.get_param('medical_ai.api_key') or '').strip(),
            'base_url': (params.get_param('medical_ai.base_url') or '').strip().rstrip('/'),
            'anonymize': params.get_param('medical_ai.anonymize', 'True') not in ('False', '0', ''),
            'log_prompts': params.get_param('medical_ai.log_prompts', 'False') not in ('False', '0', ''),
            'language': params.get_param('medical_ai.language', 'auto'),
        }

    @api.model
    def _is_configured(self):
        cfg = self._get_config()
        if not cfg['model']:
            return False
        if cfg['provider'] == 'openai_compatible':
            return bool(cfg['base_url'])
        return bool(cfg['api_key'])

    @api.model
    def _output_language(self):
        lang = self._get_config()['language']
        if lang == 'ar' or (lang == 'auto' and (self.env.user.lang or '').startswith('ar')):
            return 'Arabic (simple Modern Standard Arabic, understandable in Libya and Egypt)'
        return 'English'

    @api.model
    def _clinic_lang(self):
        """Language for texts stored by scheduled jobs (they run as OdooBot, usually in English)."""
        return self.env.company.partner_id.lang or self.env.ref('base.user_admin').lang or 'en_US'

    # ------------------------------------------------------------- privacy
    @api.model
    def _scrub_text(self, text, replacements=None):
        """Replace known identifiers by placeholders and mask phone-like numbers."""
        if not text:
            return ''
        for value, token in (replacements or {}).items():
            if value and len(value) > 2:
                text = re.sub(re.escape(value), token, text, flags=re.IGNORECASE)
        return PHONE_RE.sub('[phone]', text)

    @api.model
    def _patient_replacements(self, patient):
        """Name parts, phones and ids of a patient mapped to placeholders."""
        replacements = {}
        if not patient or not self._get_config()['anonymize']:
            return replacements
        name = patient.name or ''
        replacements[name] = '{patient}'
        for part in name.split():
            if len(part) > 2:
                replacements[part] = '{patient}'
        for field in ('phone', 'national_id', 'email'):
            value = patient[field] if field in patient._fields else False
            if value:
                replacements[value] = '[redacted]'
        return replacements

    @api.model
    def _restore_placeholders(self, text, values):
        for token, value in values.items():
            text = text.replace('{%s}' % token, value or '')
        return text

    # ------------------------------------------------------------- transport
    @api.model
    def _request(self, cfg, system, prompt, max_tokens):
        """Single HTTP round-trip to the configured provider; returns the text answer."""
        provider = cfg['provider']
        if provider == 'anthropic':
            url = (cfg['base_url'] or 'https://api.anthropic.com') + '/v1/messages'
            headers = {'x-api-key': cfg['api_key'], 'anthropic-version': '2023-06-01',
                       'content-type': 'application/json'}
            body = {'model': cfg['model'], 'max_tokens': max_tokens, 'system': system,
                    'messages': [{'role': 'user', 'content': prompt}]}
            response = requests.post(url, headers=headers, data=json.dumps(body), timeout=TIMEOUT)
            response.raise_for_status()
            return ''.join(part.get('text', '') for part in response.json().get('content', []))
        if provider == 'gemini':
            url = (cfg['base_url'] or 'https://generativelanguage.googleapis.com') + \
                f"/v1beta/models/{cfg['model']}:generateContent"
            body = {'systemInstruction': {'parts': [{'text': system}]},
                    'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                    'generationConfig': {'maxOutputTokens': max_tokens}}
            response = requests.post(url, params={'key': cfg['api_key']}, json=body, timeout=TIMEOUT)
            response.raise_for_status()
            candidates = response.json().get('candidates') or [{}]
            return ''.join(p.get('text', '') for p in candidates[0].get('content', {}).get('parts', []))
        # openai and any openai-compatible server
        base = cfg['base_url'] or 'https://api.openai.com'
        url = base + ('/chat/completions' if base.endswith('/v1') else '/v1/chat/completions')
        headers = {'content-type': 'application/json'}
        if cfg['api_key']:
            headers['Authorization'] = f"Bearer {cfg['api_key']}"
        body = {'model': cfg['model'], 'max_tokens': max_tokens,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}]}
        response = requests.post(url, headers=headers, data=json.dumps(body), timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content'] or ''

    @api.model
    def _log_error(self, vals):
        """Errors are logged on their own cursor: the UserError that follows rolls back the request."""
        if odoo_module.current_test:
            self.env['medical.ai.log'].sudo().create(dict(vals, state='error'))
            return
        with self.env.registry.cursor() as cr:
            self.env(cr=cr)['medical.ai.log'].sudo().create(dict(vals, state='error'))

    @api.model
    def _complete(self, feature, system, prompt, record=None, max_tokens=700):
        cfg = self._get_config()
        if not self._is_configured():
            raise UserError(_("Configure an AI provider and model in Settings > Medical AI first."))
        started = time.time()
        log_vals = {
            'feature': feature,
            'provider': cfg['provider'],
            'model': cfg['model'],
            'res_model': record._name if record else False,
            'res_id': record.id if record else 0,
            'prompt_chars': len(system) + len(prompt),
            'prompt': prompt if cfg['log_prompts'] else False,
        }
        try:
            text = self._request(cfg, system, prompt, max_tokens) or ''
        except requests.exceptions.RequestException as error:
            detail = str(error)
            response = getattr(error, 'response', None)
            if response is not None:
                detail = f"{response.status_code}: {response.text[:300]}"
            self._log_error(dict(log_vals, error=detail, duration=time.time() - started))
            _logger.warning("Medical AI request failed: %s", detail)
            raise UserError(_("The AI provider did not answer: %s", detail)) from error
        except (KeyError, IndexError, ValueError) as error:
            self._log_error(dict(log_vals, error=repr(error), duration=time.time() - started))
            raise UserError(_("Unexpected answer from the AI provider: %s", error)) from error
        self.env['medical.ai.log'].sudo().create(dict(log_vals, state='ok', response_chars=len(text),
                                                     duration=time.time() - started))
        return text.strip()

    # ----------------------------------------------------------- formatting
    @api.model
    def _text_to_html(self, text):
        """Render the model's light markdown (bullets, bold) as safe HTML."""
        html, in_list = [], False
        for raw in (text or '').splitlines():
            line = raw.strip()
            if not line:
                continue
            bullet = re.match(r'^([-*•]|\d+[.)])\s+(.*)$', line)
            content = escape(bullet.group(2) if bullet else line)
            content = Markup(re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', str(content)))
            if bullet:
                if not in_list:
                    html.append(Markup('<ul>'))
                    in_list = True
                html.append(Markup('<li>%s</li>') % content)
            else:
                if in_list:
                    html.append(Markup('</ul>'))
                    in_list = False
                html.append(Markup('<p>%s</p>') % content)
        if in_list:
            html.append(Markup('</ul>'))
        return Markup('').join(html)

    @api.model
    def action_test_connection(self):
        answer = self._complete('test', "Reply with the single word OK.", "Connection test.", max_tokens=10)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'type': 'success', 'title': _("AI provider reachable"),
                       'message': _("Answer: %s", answer[:80]), 'sticky': False},
        }
