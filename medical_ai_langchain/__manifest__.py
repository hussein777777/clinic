{
    'name': 'Medical Center - AI with LangChain',
    'version': '19.0.1.0.0',
    'category': 'Healthcare',
    'summary': 'LangChain engine, knowledge base (RAG), clinic assistant agent, structured claim review and a smart AI dashboard',
    'description': """
Medical Center AI with LangChain
================================
Adds LangChain on top of the Medical AI Assistant. The base module keeps working without it.

* **Engine**: briefs, follow-up messages and appeal letters run through LangChain chat models
  (OpenAI, Anthropic, Gemini, any OpenAI-compatible server), with token usage in the AI log.
  Switch back to the direct HTTP engine in Settings at any time.
* **Knowledge base (RAG)**: protocols, insurer policies, price lists and FAQs, split and embedded;
  answers quote their sources. Uses pgvector when the PostgreSQL extension is available, a plain
  table otherwise. A local embedding works with no embedding provider at all.
* **Clinic assistant**: a tool-calling agent over today's schedule, patients, results, claims,
  overdue work and the knowledge base. Tools run with the user's own access rights; patients are
  sent by file number only.
* **Structured claim review**: likely rejection reasons, fixes and missing documents as validated JSON.
* **Smart dashboard**: AI usage, tokens, cost and latency, knowledge-base coverage, AI-written insights
  on the center's numbers (rule-based when no provider is set) and the assistant in one screen.
""",
    'author': 'Hussein',
    'license': 'LGPL-3',
    'depends': ['medical_ai', 'medical_dashboard'],
    'external_dependencies': {
        'python': ['langchain_core', 'langchain_text_splitters'],
    },
    'data': [
        'security/medical_ai_langchain_security.xml',
        'security/ir.model.access.csv',
        'data/ir_config_parameter_data.xml',
        'views/medical_ai_kb_views.xml',
        'views/medical_ai_chat_views.xml',
        'views/medical_claim_line_views.xml',
        'views/medical_ai_log_views.xml',
        'views/res_config_settings_views.xml',
        'views/medical_ai_smart_dashboard_views.xml',
        'views/medical_ai_langchain_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'medical_ai_langchain/static/src/smart_dashboard/smart_dashboard.js',
            'medical_ai_langchain/static/src/smart_dashboard/smart_dashboard.xml',
            'medical_ai_langchain/static/src/smart_dashboard/smart_dashboard.scss',
        ],
        'web.assets_web_dark': [
            'medical_ai_langchain/static/src/smart_dashboard/smart_dashboard.dark.scss',
        ],
        'web.assets_tests': [
            'medical_ai_langchain/static/tests/tours/*.js',
        ],
    },
    'installable': True,
}
