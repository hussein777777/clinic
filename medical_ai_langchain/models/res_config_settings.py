from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    medical_ai_engine = fields.Selection([('langchain', 'LangChain'), ('http', 'Direct HTTP (no LangChain)')],
                                         string='AI Engine', config_parameter='medical_ai.engine', default='langchain')
    medical_ai_embedding_provider = fields.Selection([
        ('local', 'Local (no provider, works offline)'),
        ('openai', 'OpenAI'),
        ('gemini', 'Google Gemini'),
        ('openai_compatible', 'OpenAI-compatible server'),
    ], string='Embeddings', config_parameter='medical_ai.embedding_provider', default='local',
        help='Used to index the knowledge base. Changing it means re-indexing the documents.')
    medical_ai_embedding_model = fields.Char(string='Embedding Model', config_parameter='medical_ai.embedding_model')
    medical_ai_rag_top_k = fields.Integer(string='Passages per Answer', config_parameter='medical_ai.rag_top_k',
                                          default=5)
    medical_ai_price_input = fields.Float(string='Price per 1M Input Tokens (USD)',
                                          config_parameter='medical_ai.price_input')
    medical_ai_price_output = fields.Float(string='Price per 1M Output Tokens (USD)',
                                           config_parameter='medical_ai.price_output')
    medical_ai_vector_store = fields.Char(string='Vector Store', compute='_compute_medical_ai_vector_store')

    def _compute_medical_ai_vector_store(self):
        store = 'pgvector' if self.env['medical.ai.kb.chunk']._use_pgvector() else self.env._('PostgreSQL table')
        for settings in self:
            settings.medical_ai_vector_store = store
