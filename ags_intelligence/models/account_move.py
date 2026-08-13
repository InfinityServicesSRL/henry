# -*- coding: utf-8 -*-
from odoo import models, fields, api


class AccountMove(models.Model):
    """Registro de consumo electrico en la factura de la distribuidora.

    IDEA DE DISENO: el kWh viaja pegado a un proceso que no se interrumpe.
    La factura de energia entra a Odoo todos los meses porque hay que pagarla;
    anotar el consumo ahi garantiza que el dato exista, a diferencia de pedirle
    a alguien que lea el medidor cada mes.

    De paso queda el costo real por kWh, que en Republica Dominicana es un
    indicador por derecho propio: la energia industrial esta entre las mas
    caras de la region.
    """
    _inherit = "account.move"

    ags_kwh_consumidos = fields.Float(
        string="Consumo (kWh)",
        digits=(16, 2),
        tracking=True,
        help="kWh facturados en el periodo. Se toma de la factura de la "
             "distribuidora y alimenta los indicadores de consumo por "
             "tonelada y costo por kWh.",
    )
    ags_es_factura_energia = fields.Boolean(
        string="Es factura de energia",
        compute="_compute_es_factura_energia",
        store=False,
        help="Verdadero cuando el proveedor coincide con el declarado en la "
             "configuracion de AG Intelligence.",
    )
    ags_costo_kwh = fields.Float(
        string="Costo por kWh",
        compute="_compute_costo_kwh",
        digits=(16, 4),
        store=False,
    )

    @api.depends("partner_id", "move_type")
    def _compute_es_factura_energia(self):
        config = self.env["ags.config"].search(
            [("company_id", "=", self.env.company.id)], limit=1)
        proveedor = config.proveedor_energia_id if config else False
        for rec in self:
            rec.ags_es_factura_energia = bool(
                proveedor
                and rec.move_type == "in_invoice"
                and rec.partner_id == proveedor
            )

    @api.depends("ags_kwh_consumidos", "amount_untaxed")
    def _compute_costo_kwh(self):
        for rec in self:
            rec.ags_costo_kwh = (
                rec.amount_untaxed / rec.ags_kwh_consumidos
                if rec.ags_kwh_consumidos else 0.0
            )
