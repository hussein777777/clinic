from odoo import fields, models

from .medical_ai_service import PROVIDERS


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    medical_ai_provider = fields.Selection(PROVIDERS, string='AI Provider',
                                          config_parameter='medical_ai.provider', default='openai')
    medical_ai_model = fields.Char(string='Model', config_parameter='medical_ai.model',
                                 help='Model identifier exactly as the provider names it.')
    medical_ai_api_key = fields.Char(string='API Key', config_parameter='medical_ai.api_key')
    medical_ai_base_url = fields.Char(string='Base URL', config_parameter='medical_ai.base_url',
                                    help='Only for self-hosted / OpenAI-compatible servers, e.g. http://localhost:11434/v1')
    medical_ai_anonymize = fields.Boolean(string='Hide patient identity', config_parameter='medical_ai.anonymize',
                                         default=True,
                                         help='Names, phones and IDs are replaced by placeholders before any text '
                                              'is sent, and put back in the answer locally.')
    medical_ai_log_prompts = fields.Boolean(string='Keep prompts in the log', config_parameter='medical_ai.log_prompts')
    medical_ai_language = fields.Selection([('auto', "User's language"), ('ar', 'Arabic'), ('en', 'English')],
                                          string='AI Output Language', config_parameter='medical_ai.language',
                                          default='auto')

    def action_medical_ai_test(self):
        self.set_values()
        return self.env['medical.ai.service'].action_test_connection()

    def action_medical_noshow_backtest(self):
        return self.env['medical.visit'].action_noshow_backtest()
