import base64
import io
import logging
import math
import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL, html2plaintext
from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:  # pragma: no cover - declared in external_dependencies
    RecursiveCharacterTextSplitter = None

CATEGORIES = [
    ('protocol', 'Clinical protocol'),
    ('insurer_policy', 'Insurer policy'),
    ('price_list', 'Prices & packages'),
    ('admin', 'Admin procedure'),
    ('faq', 'Patient FAQ'),
    ('other', 'Other'),
]
SEPARATORS = ["\n\n", "\n", ". ", "؟ ", "? ", "، ", ", ", " ", ""]
TEXT_TYPES = ('text/plain', 'text/markdown', 'text/csv', 'application/json')
SENTENCE_END = re.compile(r'[.!?؟。]\s')


def html_to_text(html):
    """Plain text of an HTML body, without the *bold* / _italic_ markers html2plaintext adds."""
    text = html2plaintext(html or '')
    return re.sub(r'(?<!\w)[*_](\S(?:.*?\S)?)[*_](?!\w)', r'\1', text)


def excerpt(text, limit=320):
    """Start of a passage cut at the last full sentence within `limit` characters."""
    text = ' '.join((text or '').split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [m.end() for m in SENTENCE_END.finditer(cut + ' ')]
    return (cut[:ends[-1]].strip() if ends and ends[-1] > limit // 3 else cut.rsplit(' ', 1)[0] + '…')


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class MedicalAiKbDocument(models.Model):
    _name = 'medical.ai.kb.document'
    _description = 'Knowledge Base Document'
    _inherit = ['mail.thread']
    _order = 'category, name'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    category = fields.Selection(CATEGORIES, required=True, default='protocol', tracking=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company,
                                 help='Leave empty to share the document with every company.')
    department_id = fields.Many2one('medical.department', string='Clinic',
                                    help='Questions asked for this clinic search its documents first.')
    insurer_id = fields.Many2one('res.partner', string='Insurer', domain=[('is_insurer', '=', True)])
    source_type = fields.Selection([('file', 'File (PDF or text)'), ('text', 'Written here')], default='text',
                                   required=True)
    datas = fields.Binary(string='File', attachment=True)
    filename = fields.Char()
    body = fields.Html(sanitize=True)
    state = fields.Selection([('draft', 'Not indexed'), ('indexed', 'Indexed'), ('error', 'Error')],
                             default='draft', required=True, readonly=True, copy=False, tracking=True)
    error_message = fields.Char(readonly=True, copy=False)
    chunk_ids = fields.One2many('medical.ai.kb.chunk', 'document_id', string='Passages', readonly=True)
    chunk_count = fields.Integer(compute='_compute_chunk_count', store=True, string='Passage Count')
    char_count = fields.Integer(string='Characters', readonly=True, copy=False)
    indexed_at = fields.Datetime(readonly=True, copy=False)
    embedding_signature = fields.Char(string='Embedding Model', readonly=True, copy=False)
    is_outdated = fields.Boolean(compute='_compute_is_outdated', string='Needs Re-indexing')

    @api.depends('chunk_ids')
    def _compute_chunk_count(self):
        for doc in self:
            doc.chunk_count = len(doc.chunk_ids)

    def _compute_is_outdated(self):
        _embeddings, signature = self.env['medical.ai.service']._lc_embeddings()
        for doc in self:
            doc.is_outdated = doc.state == 'indexed' and doc.embedding_signature != signature

    def write(self, vals):
        if {'body', 'datas'} & set(vals):
            vals = dict(vals, state='draft')
        return super().write(vals)

    # ------------------------------------------------------------- extraction
    def _extract_pages(self):
        """[(page number or 0, text)] of the document."""
        self.ensure_one()
        if self.source_type == 'text':
            return [(0, html_to_text(self.body))]
        if not self.datas:
            raise UserError(_("Upload a file for %s.", self.name))
        raw = base64.b64decode(self.datas)
        name = (self.filename or '').lower()
        if raw[:4] == b'%PDF' or name.endswith('.pdf'):
            try:
                from pypdf import PdfReader
            except ImportError as error:
                raise UserError(_("Reading PDF files needs the Python package pypdf on the server.")) from error
            reader = PdfReader(io.BytesIO(raw))
            return [(index + 1, page.extract_text() or '') for index, page in enumerate(reader.pages)]
        if name.endswith(('.html', '.htm')):
            return [(0, html_to_text(raw.decode('utf-8', errors='replace')))]
        if name.endswith(('.txt', '.md', '.csv', '.json')) or not name:
            return [(0, raw.decode('utf-8', errors='replace'))]
        raise UserError(_("%s: only PDF, text, Markdown, CSV and HTML files can be indexed. "
                          "For Word files, paste the text in the document instead.", self.filename))

    def _split(self, pages):
        splitter = RecursiveCharacterTextSplitter(chunk_size=900, chunk_overlap=120, separators=SEPARATORS)
        chunks = []
        for page, text in pages:
            for piece in splitter.split_text(text or ''):
                piece = piece.strip()
                if len(piece) >= 20:
                    chunks.append((page, piece))
        return chunks

    # ---------------------------------------------------------------- indexing
    def action_index(self):
        service = self.env['medical.ai.service']
        embeddings, signature = service._lc_embeddings()
        Chunk = self.env['medical.ai.kb.chunk'].sudo()
        for doc in self:
            try:
                pages = doc._extract_pages()
                chunks = doc._split(pages)
                if not chunks:
                    raise UserError(_("No text found in %s.", doc.name))
            except UserError as error:
                doc.write({'state': 'error', 'error_message': str(error)[:250]})
                continue
            texts = [text for _page, text in chunks]
            if signature.startswith('local:'):
                vectors = embeddings.embed_documents(texts)
            else:
                vectors = service._lc_run('embedding', lambda _s, _u: embeddings.embed_documents(texts),
                                          record=doc, prompt_chars=sum(len(t) for t in texts))
            doc.chunk_ids.sudo().unlink()
            records = Chunk.create([{
                'document_id': doc.id, 'sequence': index, 'page': page, 'content': text, 'signature': signature,
            } for index, (page, text) in enumerate(chunks)])
            records._store_vectors(vectors)
            doc.write({'state': 'indexed', 'error_message': False, 'indexed_at': fields.Datetime.now(),
                       'embedding_signature': signature, 'char_count': sum(len(t) for t in texts)})
        return True

    @api.model
    def _cron_index_pending(self):
        self.search([('state', '=', 'draft')], limit=50).action_index()

    # ---------------------------------------------------------------- retrieval
    @api.model
    def _kb_search(self, query, limit=None, department_id=False, category=False):
        """Best passages for `query` among the documents the user can read: [(chunk, score)]."""
        service = self.env['medical.ai.service']
        limit = limit or service._get_config()['rag_top_k']
        domain = [('state', '=', 'indexed')]
        if category:
            domain.append(('category', '=', category))
        docs = self.search(domain)
        if department_id:
            # the clinic's own documents and the center-wide ones
            docs = docs.filtered(lambda d: not d.department_id or d.department_id.id == int(department_id))
        if not docs or not (query or '').strip():
            return []
        embeddings, signature = service._lc_embeddings()
        if signature.startswith('local:'):
            vector = embeddings.embed_query(query)
        else:
            vector = service._lc_run('embedding', lambda _s, _u: embeddings.embed_query(query),
                                     prompt_chars=len(query))
        return self.env['medical.ai.kb.chunk']._nearest(vector, signature, docs.ids, limit)

    @api.model
    def _kb_answer(self, question, department_id=False):
        """Answer from the knowledge base, with numbered sources. Extractive when no provider is set."""
        service = self.env['medical.ai.service']
        hits = self._relevant(self._kb_search(question, department_id=department_id))
        sources = [{
            'n': index + 1,
            'document_id': chunk.document_id.id,
            'document': chunk.document_id.name,
            'category': dict(CATEGORIES).get(chunk.document_id.category),
            'page': chunk.page,
            'excerpt': excerpt(chunk.content, 400),
            'score': round(score, 3),
        } for index, (chunk, score) in enumerate(hits)]
        if not hits:
            return {'answer': _("No document in the knowledge base covers this question."), 'sources': [],
                    'mode': 'none'}
        if not (service._is_configured() and service._lc_enabled()):
            text = '\n'.join(f"- [{s['n']}] {s['document']}: {excerpt(s['excerpt'], 280)}" for s in sources[:3])
            return {'answer': _("Closest passages (no AI provider set, so no written answer):") + '\n' + text,
                    'sources': sources, 'mode': 'extractive'}
        context = '\n\n'.join(f"[{s['n']}] {s['document']}" + (f" (p. {s['page']})" if s['page'] else '')
                              + f"\n{hit.content}" for s, (hit, _score) in zip(sources, hits))
        system = (
            "You answer staff questions at a medical center using only the numbered sources below. "
            "Cite every statement with its source number in square brackets, like [2]. If the sources do not "
            "answer the question, say so plainly and do not guess. Never give a diagnosis or a dose for a "
            f"specific patient. Be brief and practical. Write in {service._output_language()}.\n\nSources:\n{context}"
        )
        question_safe = service._scrub_text(question)

        def runner(svc, usage):
            llm = svc._lc_chat_model(svc._get_config(), max_tokens=700)
            message = llm.invoke([SystemMessage(content=system), HumanMessage(content=question_safe)])
            svc._lc_record_usage(message)
            from .lc_engine import message_text
            return message_text(message).strip()

        answer = service._lc_run('rag', runner, prompt_chars=len(system) + len(question_safe), prompt=question_safe)
        return {'answer': answer, 'sources': sources, 'mode': 'generative'}

    @api.model
    def _relevant(self, hits):
        """Drop weak matches: below an absolute floor, or far below the best passage."""
        if not hits:
            return []
        best = hits[0][1]
        return [(chunk, score) for chunk, score in hits if score >= max(0.12, best * 0.6)]

    def action_open_passages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Passages"),
            'res_model': 'medical.ai.kb.chunk',
            'view_mode': 'list,form',
            'domain': [('document_id', '=', self.id)],
        }


class MedicalAiKbChunk(models.Model):
    _name = 'medical.ai.kb.chunk'
    _description = 'Knowledge Base Passage'
    _order = 'document_id, sequence'

    document_id = fields.Many2one('medical.ai.kb.document', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='document_id.company_id', store=True)
    sequence = fields.Integer()
    page = fields.Integer()
    content = fields.Text(required=True)
    signature = fields.Char(index=True)

    def init(self):
        super().init()
        cr = self.env.cr
        if not column_exists(cr, self._table, 'embedding'):
            cr.execute(SQL("ALTER TABLE %s ADD COLUMN embedding double precision[]", SQL.identifier(self._table)))
        if self._pgvector_available() and not column_exists(cr, self._table, 'embedding_vec'):
            cr.execute(SQL("ALTER TABLE %s ADD COLUMN embedding_vec vector", SQL.identifier(self._table)))

    @api.model
    def _pgvector_available(self):
        """True when the pgvector extension is (or can be) enabled in this database."""
        cr = self.env.cr
        cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        if cr.fetchone():
            return True
        cr.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        if not cr.fetchone():
            return False
        try:
            with cr.savepoint():
                cr.execute("CREATE EXTENSION IF NOT EXISTS vector")
            return True
        except Exception:  # noqa: BLE001 - not superuser: fall back to the plain column
            _logger.info("pgvector is installed but cannot be enabled by this database user; using the plain table.")
            return False

    @api.model
    def _use_pgvector(self):
        return column_exists(self.env.cr, self._table, 'embedding_vec')

    def _store_vectors(self, vectors):
        use_vec = self._use_pgvector()
        for chunk, vector in zip(self, vectors):
            vector = [float(v) for v in vector]
            if use_vec:
                self.env.cr.execute(SQL(
                    "UPDATE %s SET embedding = %s, embedding_vec = %s::vector WHERE id = %s",
                    SQL.identifier(self._table), vector, '[' + ','.join(repr(v) for v in vector) + ']', chunk.id))
            else:
                self.env.cr.execute(SQL("UPDATE %s SET embedding = %s WHERE id = %s",
                                        SQL.identifier(self._table), vector, chunk.id))

    @api.model
    def _nearest(self, vector, signature, document_ids, limit):
        """[(chunk, cosine similarity)] best first, among the given documents' passages."""
        cr = self.env.cr
        if self._use_pgvector():
            literal = '[' + ','.join(repr(float(v)) for v in vector) + ']'
            cr.execute(SQL(
                """SELECT id, 1 - (embedding_vec <=> %(vec)s::vector) AS score
                     FROM %(table)s
                    WHERE document_id = ANY(%(docs)s) AND signature = %(sig)s AND embedding_vec IS NOT NULL
                 ORDER BY embedding_vec <=> %(vec)s::vector
                    LIMIT %(limit)s""",
                vec=literal, table=SQL.identifier(self._table), docs=list(document_ids), sig=signature, limit=limit))
            rows = cr.fetchall()
        else:
            cr.execute(SQL(
                "SELECT id, embedding FROM %s WHERE document_id = ANY(%s) AND signature = %s AND embedding IS NOT NULL",
                SQL.identifier(self._table), list(document_ids), signature))
            rows = sorted(((cid, cosine(vector, emb)) for cid, emb in cr.fetchall()), key=lambda r: -r[1])[:limit]
        chunks = self.browse([cid for cid, _score in rows])
        return [(chunk, float(score)) for chunk, (_cid, score) in zip(chunks, rows)]

    def _source_html(self):
        self.ensure_one()
        return Markup('<b>%s</b>%s<br/>%s') % (
            self.document_id.name, f" — p. {self.page}" if self.page else '', self.content[:300])
