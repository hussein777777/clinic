from . import models
from . import wizard


def post_init_hook(env):
    for company in env['res.company'].search([]):
        if company.chart_template:
            company._medical_setup_accounting()
