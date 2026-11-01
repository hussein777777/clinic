from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MedicalProcedureConsumable(models.Model):
    _name = 'medical.procedure.consumable'
    _description = 'Materials consumed by a medical procedure'

    product_tmpl_id = fields.Many2one('product.template', string='Procedure', required=True,
                                      ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', string='Material', required=True,
                                 domain=[('type', '=', 'consu')])
    quantity = fields.Float(required=True, default=1.0, digits='Product Unit')
    uom_id = fields.Many2one(related='product_id.uom_id')
    tracking = fields.Selection(related='product_id.tracking')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    medical_consumable_ids = fields.One2many('medical.procedure.consumable', 'product_tmpl_id',
                                            string='Consumed Materials')


class ResCompany(models.Model):
    _inherit = 'res.company'

    medical_consumption_location_id = fields.Many2one('stock.location', string='Clinic Consumption Location')
    medical_stock_location_id = fields.Many2one('stock.location', string='Clinic Stock Location')

    def _medical_stock_locations(self):
        self.ensure_one()
        if not self.medical_stock_location_id:
            warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.id)], limit=1)
            if not warehouse:
                raise UserError(_("Create a warehouse for %s first.", self.name))
            self.sudo().medical_stock_location_id = warehouse.lot_stock_id
        if not self.medical_consumption_location_id:
            parent = self.env.ref('stock.stock_location_locations_virtual', raise_if_not_found=False)
            self.sudo().medical_consumption_location_id = self.env['stock.location'].sudo().create({
                'name': _('Clinic Consumption'),
                'usage': 'inventory',
                'location_id': parent.id if parent else False,
                'company_id': self.id,
            })
        return self.medical_stock_location_id, self.medical_consumption_location_id


class StockMove(models.Model):
    _inherit = 'stock.move'

    medical_line_id = fields.Many2one('medical.treatment.line', string='Medical Treatment', index='btree_not_null')


class MedicalTreatmentLine(models.Model):
    _inherit = 'medical.treatment.line'

    tracked_material_id = fields.Many2one('product.product', compute='_compute_tracked_material',
                                          help='Serial / lot tracked material (implant) used by this procedure.')
    lot_id = fields.Many2one('stock.lot', string='Implant / Lot',
                             domain="[('product_id', '=', tracked_material_id)]", copy=False)
    stock_move_ids = fields.One2many('stock.move', 'medical_line_id', string='Material Moves')

    @api.depends('product_id')
    def _compute_tracked_material(self):
        for line in self:
            tracked = line.product_id.medical_consumable_ids.filtered(lambda c: c.product_id.tracking != 'none')
            line.tracked_material_id = tracked[:1].product_id

    def _medical_check_before_execute(self):
        super()._medical_check_before_execute()
        missing = self.filtered(lambda l: l.tracked_material_id and not l.lot_id)
        if missing:
            raise UserError(_("Select the serial / lot number used for: %s",
                              ", ".join(missing.mapped('display_name'))))

    def _after_execute(self):
        res = super()._after_execute()
        self.filtered(lambda l: l.product_id.medical_consumable_ids)._consume_materials()
        return res

    def _consume_materials(self):
        Move = self.env['stock.move'].sudo()
        for line in self:
            src, dest = line.company_id._medical_stock_locations()
            vals_list = []
            for consumable in line.product_id.medical_consumable_ids:
                vals_list.append({
                    'product_id': consumable.product_id.id,
                    'product_uom_qty': consumable.quantity,
                    'product_uom': consumable.product_id.uom_id.id,
                    'location_id': src.id,
                    'location_dest_id': dest.id,
                    'origin': f"{line.visit_id.name or ''} {line.display_name}".strip(),
                    'company_id': line.company_id.id,
                    'medical_line_id': line.id,
                })
            moves = Move.create(vals_list)
            moves._action_confirm()
            moves._action_assign()
            for move in moves:
                if move.product_id.tracking != 'none':
                    move.move_line_ids.unlink()
                    self.env['stock.move.line'].sudo().create({
                        'move_id': move.id,
                        'product_id': move.product_id.id,
                        'product_uom_id': move.product_uom.id,
                        'location_id': src.id,
                        'location_dest_id': dest.id,
                        'lot_id': line.lot_id.id,
                        'quantity': move.product_uom_qty,
                    })
                else:
                    move.quantity = move.product_uom_qty
            moves.picked = True
            moves._action_done()
