import hashlib
import logging
import math
import re
import time
import unicodedata

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from langchain_core.embeddings import Embeddings
    from langchain_core.messages import HumanMessage, SystemMessage
except ImportError:  # pragma: no cover - declared in external_dependencies
    Embeddings = object
    HumanMessage = SystemMessage = None

LC_TIMEOUT = 90
LOCAL_DIM = 512
ARABIC_DIACRITICS = re.compile(r'[ؐ-ًؚ-ٰٟۖ-ۭـ]')
WORD_RE = re.compile(r'\w+', re.UNICODE)

FEATURES = [
    ('rag', 'Knowledge question'),
    ('agent', 'Clinic assistant'),
    ('claim_review', 'Claim review'),
    ('insight', 'Dashboard insights'),
    ('embedding', 'Knowledge indexing'),
]


class LangChainProviderError(requests.exceptions.RequestException):
    """A LangChain / provider SDK failure, shaped so the base module's error handling and logging apply."""


def normalize_text(text):
    """Lower-case, strip Arabic diacritics and tatweel, unify alef / yaa / taa marbuta forms."""
    text = unicodedata.normalize('NFKC', text or '').lower()
    text = ARABIC_DIACRITICS.sub('', text)
    return (text.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
            .replace('ى', 'ي').replace('ة', 'ه').replace('ؤ', 'و').replace('ئ', 'ي'))


class LocalHashEmbeddings(Embeddings):
    """Offline embedding: hashed words and character trigrams, sub-linear weights, L2-normalised.

    It needs no provider and no extra package, works for Arabic and English, and is deterministic.
    It matches wording rather than meaning, so a real embedding model gives better recall.
    """

    def __init__(self, dim=LOCAL_DIM):
        self.dim = dim

    def _features(self, text):
        words = [w for w in WORD_RE.findall(normalize_text(text)) if len(w) > 1]
        for word in words:
            yield 'w:' + word, 1.0
            padded = f'#{word}#'
            for i in range(len(padded) - 2):
                yield 'c:' + padded[i:i + 3], 0.35
        for first, second in zip(words, words[1:]):
            yield f'b:{first}_{second}', 0.6

    def _embed(self, text):
        counts = {}
        for feature, weight in self._features(text):
            digest = hashlib.md5(feature.encode()).digest()
            index = int.from_bytes(digest[:4], 'little') % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            counts[index] = counts.get(index, 0.0) + sign * weight
        vector = [0.0] * self.dim
        for index, value in counts.items():
            vector[index] = math.copysign(1 + math.log(abs(value)), value) if value else 0.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


def message_text(message):
    """Text of an AI message whose content may be a string or a list of content blocks."""
    content = getattr(message, 'content', message)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get('type') == 'text':
            parts.append(block.get('text', ''))
    return ''.join(parts)


class MedicalAiLog(models.Model):
    _inherit = 'medical.ai.log'

    feature = fields.Selection(selection_add=FEATURES, ondelete={key: 'cascade' for key, _label in FEATURES})
    engine = fields.Selection([('http', 'Direct HTTP'), ('langchain', 'LangChain')], default='http')
    input_tokens = fields.Integer()
    output_tokens = fields.Integer()
    tool_calls = fields.Integer(help='Tools the clinic assistant called to answer.')
    cost = fields.Float(string='Estimated Cost (USD)', digits=(16, 5),
                        help='Tokens multiplied by the prices per million tokens set in Settings.')

    @api.model_create_multi
    def create(self, vals_list):
        usage = self.env.context.get('medical_ai_usage')
        params = self.env['ir.config_parameter'].sudo()
        price_in = float(params.get_param('medical_ai.price_input', 0) or 0)
        price_out = float(params.get_param('medical_ai.price_output', 0) or 0)
        for vals in vals_list:
            if usage:
                for key in ('engine', 'input_tokens', 'output_tokens', 'tool_calls'):
                    if key in usage and key not in vals:
                        vals[key] = usage[key]
            if vals.get('input_tokens') or vals.get('output_tokens'):
                vals.setdefault('cost', ((vals.get('input_tokens') or 0) * price_in
                                         + (vals.get('output_tokens') or 0) * price_out) / 1e6)
        return super().create(vals_list)


class MedicalAiService(models.AbstractModel):
    _inherit = 'medical.ai.service'

    # ------------------------------------------------------------------ config
    @api.model
    def _get_config(self):
        cfg = super()._get_config()
        params = self.env['ir.config_parameter'].sudo()
        cfg.update({
            'engine': params.get_param('medical_ai.engine', 'langchain'),
            'embedding_provider': params.get_param('medical_ai.embedding_provider', 'local'),
            'embedding_model': (params.get_param('medical_ai.embedding_model') or '').strip(),
            'rag_top_k': int(params.get_param('medical_ai.rag_top_k', 5) or 5),
        })
        return cfg

    @api.model
    def _lc_enabled(self, cfg=None):
        return (cfg or self._get_config())['engine'] == 'langchain' and HumanMessage is not None

    # ------------------------------------------------------------------ models
    @api.model
    def _lc_import(self, module, package):
        try:
            return __import__(module, fromlist=['_'])
        except ImportError as error:
            raise UserError(_("The Python package %(package)s is missing on the server. Install it with: "
                              "pip install %(package)s", package=package)) from error

    @api.model
    def _lc_chat_model(self, cfg, max_tokens=700):
        """LangChain chat model for the configured provider. Tests replace this method with a fake model."""
        provider = cfg['provider']
        if provider == 'anthropic':
            lib = self._lc_import('langchain_anthropic', 'langchain-anthropic')
            kwargs = {'model': cfg['model'], 'api_key': cfg['api_key'], 'max_tokens': max_tokens,
                      'timeout': LC_TIMEOUT, 'max_retries': 1}
            if cfg['base_url']:
                kwargs['base_url'] = cfg['base_url']
            return lib.ChatAnthropic(**kwargs)
        if provider == 'gemini':
            lib = self._lc_import('langchain_google_genai', 'langchain-google-genai')
            return lib.ChatGoogleGenerativeAI(model=cfg['model'], google_api_key=cfg['api_key'],
                                              max_output_tokens=max_tokens, timeout=LC_TIMEOUT, max_retries=1)
        lib = self._lc_import('langchain_openai', 'langchain-openai')
        kwargs = {'model': cfg['model'], 'api_key': cfg['api_key'] or 'not-needed', 'max_tokens': max_tokens,
                  'timeout': LC_TIMEOUT, 'max_retries': 1}
        if cfg['base_url']:
            base = cfg['base_url']
            kwargs['base_url'] = base if base.endswith('/v1') else base + '/v1'
        return lib.ChatOpenAI(**kwargs)

    @api.model
    def _lc_embeddings(self, cfg=None):
        """Embedding model and its signature (stored on indexed documents to detect a model change)."""
        cfg = cfg or self._get_config()
        provider = cfg['embedding_provider']
        model = cfg['embedding_model']
        if provider == 'local' or not model:
            return LocalHashEmbeddings(), f'local:hash-{LOCAL_DIM}'
        if provider == 'gemini':
            lib = self._lc_import('langchain_google_genai', 'langchain-google-genai')
            return lib.GoogleGenerativeAIEmbeddings(model=model, google_api_key=cfg['api_key']), f'gemini:{model}'
        lib = self._lc_import('langchain_openai', 'langchain-openai')
        kwargs = {'model': model, 'api_key': cfg['api_key'] or 'not-needed'}
        if provider == 'openai_compatible' and cfg['base_url']:
            base = cfg['base_url']
            kwargs['base_url'] = base if base.endswith('/v1') else base + '/v1'
            kwargs['check_embedding_ctx_length'] = False
        return lib.OpenAIEmbeddings(**kwargs), f'{provider}:{model}'

    # ------------------------------------------------------------------ usage
    @api.model
    def _lc_record_usage(self, message):
        usage = self.env.context.get('medical_ai_usage')
        if usage is None:
            return
        usage['engine'] = 'langchain'
        meta = getattr(message, 'usage_metadata', None) or {}
        usage['input_tokens'] = usage.get('input_tokens', 0) + (meta.get('input_tokens') or 0)
        usage['output_tokens'] = usage.get('output_tokens', 0) + (meta.get('output_tokens') or 0)

    @api.model
    def _complete(self, feature, system, prompt, record=None, max_tokens=700):
        usage = {'engine': 'langchain' if self._lc_enabled() else 'http'}
        return super(MedicalAiService, self.with_context(medical_ai_usage=usage))._complete(
            feature, system, prompt, record=record, max_tokens=max_tokens)

    @api.model
    def _request(self, cfg, system, prompt, max_tokens):
        """The base module's single round-trip, through LangChain when that engine is selected."""
        if not self._lc_enabled(cfg):
            return super()._request(cfg, system, prompt, max_tokens)
        llm = self._lc_chat_model(cfg, max_tokens)
        try:
            message = llm.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
        except UserError:
            raise
        except Exception as error:  # provider SDKs raise their own exception types
            raise LangChainProviderError(f"{type(error).__name__}: {str(error)[:300]}") from error
        self._lc_record_usage(message)
        return message_text(message)

    # -------------------------------------------------------- generic runner
    @api.model
    def _lc_run(self, feature, runner, record=None, prompt_chars=0, prompt=False):
        """Run `runner(usage)` (a LangChain call) with timing, token usage and the AI log.

        `runner` fills `usage` (tokens, tool calls) and returns the result. Provider failures are logged
        on their own cursor and raised as a UserError, like the base module does.
        """
        cfg = self._get_config()
        usage = {'engine': 'langchain', 'input_tokens': 0, 'output_tokens': 0, 'tool_calls': 0}
        service = self.with_context(medical_ai_usage=usage)
        started = time.time()
        log_vals = {
            'feature': feature,
            'provider': cfg['provider'] if feature != 'embedding' else cfg['embedding_provider'],
            'model': cfg['model'] if feature != 'embedding' else (cfg['embedding_model'] or 'local'),
            'res_model': record._name if record else False,
            'res_id': record.id if record else 0,
            'prompt_chars': prompt_chars,
            'prompt': prompt if cfg['log_prompts'] else False,
        }
        try:
            result = runner(service, usage)
        except UserError:
            raise
        except Exception as error:
            detail = f"{type(error).__name__}: {str(error)[:300]}"
            service._log_error(dict(log_vals, error=detail, duration=time.time() - started))
            _logger.warning("Medical AI (%s) failed: %s", feature, detail)
            raise UserError(_("The AI provider did not answer: %s", detail)) from error
        response_chars = len(result) if isinstance(result, str) else 0
        service.env['medical.ai.log'].sudo().create(dict(log_vals, state='ok', response_chars=response_chars,
                                                        duration=time.time() - started))
        return result

    @api.model
    def _require_provider(self):
        if not self._is_configured():
            raise UserError(_("Configure an AI provider and model in Settings > Medical AI first."))
        if not self._lc_enabled():
            raise UserError(_("This feature needs the LangChain engine: select it in Settings > Medical AI."))
