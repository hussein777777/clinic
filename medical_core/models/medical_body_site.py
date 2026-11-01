from odoo import fields, models

LATERALITY = [
    ('right', 'Right'),
    ('left', 'Left'),
    ('bilateral', 'Both Sides'),
]

BODY_REGIONS = [
    ('head', 'Head & Face'),
    ('eye', 'Eye'),
    ('neck', 'Neck'),
    ('spine', 'Spine'),
    ('trunk', 'Chest & Abdomen'),
    ('pelvis', 'Pelvis & Hip'),
    ('upper_limb', 'Upper Limb'),
    ('lower_limb', 'Lower Limb'),
    ('skin', 'Skin (general)'),
    ('other', 'Other'),
]


class MedicalBodySite(models.Model):
    _name = 'medical.body.site'
    _description = 'Body Site'
    _order = 'region, sequence, name'
    _parent_store = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    region = fields.Selection(BODY_REGIONS, required=True, default='other')
    has_laterality = fields.Boolean(string='Has Sides', help='Left / right must be given (knee, eye, hand...).')
    parent_id = fields.Many2one('medical.body.site', index=True, ondelete='cascade')
    parent_path = fields.Char(index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code)', 'The body site code must be unique.')
