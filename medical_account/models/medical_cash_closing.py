from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command


class MedicalCashClosing(models.Model):
    _name = 'medical.cash.closing'
    _description = 'Daily Cash Closing'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    journal_id = fields.Many2one('account.journal', required=True, domain=[('type', '=', 'cash')])
    user_id = fields.Many2one('res.users', string='Cashier', required=True, default=lambda self: self.env.user)
    company_id = fields.Many2one(related='journal_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    payment_ids = fields.Many2many('account.payment', compute='_compute_expected', string='Payments')
    amount_in = fields.Monetary(compute='_compute_expected', string='Collected')
    amount_out = fields.Monetary(compute='_compute_expected', string='Refunded')
    amount_expected = fields.Monetary(compute='_compute_expected', string='Expected in Drawer')
    amount_counted = fields.Monetary(string='Counted', tracking=True)
    difference = fields.Monetary(compute='_compute_difference', store=True)
    state = fields.Selection([('draft', 'Open'), ('closed', 'Closed')], default='draft', tracking=True)
    difference_move_id = fields.Many2one('account.move', readonly=True)
    note = fields.Text()

    _uniq = models.Constraint('UNIQUE(date, journal_id, user_id)', 'This cashier already has a closing for that day.')

    @api.depends('date', 'journal_id', 'user_id')
    def _compute_name(self):
        for closing in self:
            closing.name = f"{closing.journal_id.code or ''}/{closing.user_id.name or ''}/{closing.date or ''}"

    def _payment_domain(self):
        self.ensure_one()
        return [
            ('journal_id', '=', self.journal_id.id),
            ('date', '=', self.date),
            ('create_uid', '=', self.user_id.id),
            ('state', 'in', ('in_process', 'paid')),
            ('partner_type', '=', 'customer'),
        ]

    @api.depends('date', 'journal_id', 'user_id')
    def _compute_expected(self):
        for closing in self:
            if not (closing.journal_id and closing.date and closing.user_id):
                closing.payment_ids = False
                closing.amount_in = closing.amount_out = closing.amount_expected = 0.0
                continue
            payments = self.env['account.payment'].search(closing._payment_domain())
            closing.payment_ids = payments
            closing.amount_in = sum(payments.filtered(lambda p: p.payment_type == 'inbound').mapped('amount'))
            closing.amount_out = sum(payments.filtered(lambda p: p.payment_type == 'outbound').mapped('amount'))
            closing.amount_expected = closing.amount_in - closing.amount_out

    @api.depends('amount_counted', 'amount_in', 'amount_out')
    def _compute_difference(self):
        for closing in self:
            closing.difference = closing.amount_counted - closing.amount_expected

    def action_close(self):
        for closing in self:
            if closing.state != 'draft':
                continue
            diff = closing.amount_counted - closing.amount_expected
            if not closing.currency_id.is_zero(diff):
                journal = closing.journal_id
                counterpart = journal.profit_account_id if diff > 0 else journal.loss_account_id
                if not counterpart or not journal.default_account_id:
                    raise UserError(_("Set the profit / loss accounts on journal %s.", journal.name))
                move = self.env['account.move'].create({
                    'move_type': 'entry',
                    'journal_id': journal.id,
                    'date': closing.date,
                    'ref': _("Cash difference %s", closing.name),
                    'medical_move_kind': 'cash_diff',
                    'line_ids': [
                        Command.create({'account_id': journal.default_account_id.id, 'name': closing.name,
                                        'debit': diff if diff > 0 else 0.0, 'credit': -diff if diff < 0 else 0.0}),
                        Command.create({'account_id': counterpart.id, 'name': closing.name,
                                        'debit': -diff if diff < 0 else 0.0, 'credit': diff if diff > 0 else 0.0}),
                    ],
                })
                move.action_post()
                closing.difference_move_id = move
            closing.state = 'closed'
