# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AgDesempenoSnapshot(models.Model):
    """Foto mensual del desempeño de un cliente.

    Sirve para tres cosas que la ficha viva no puede dar: la serie de 12 meses
    que dibujan los gráficos, la comparación contra el propio pasado del
    cliente, y la trazabilidad de por qué un cliente tenía tal grado en tal
    mes cuando alguien lo pregunte tres meses después.
    """
    _name = "ag.desempeno.snapshot"
    _description = "Snapshot mensual de desempeño de cliente"
    _order = "fecha_corte desc, venta_neta desc"
    _rec_name = "display_name"

    partner_id = fields.Many2one(
        "res.partner", string="Cliente", required=True, index=True,
        ondelete="cascade")
    company_id = fields.Many2one(
        "res.company", string="Compañía", required=True, index=True,
        default=lambda self: self.env.company)
    fecha_corte = fields.Date(
        string="Corte", required=True, index=True,
        help="Último día del mes evaluado.")
    periodo = fields.Char(string="Período", index=True)

    # --- del mes ---
    venta_neta = fields.Monetary(string="Venta neta del mes", currency_field="currency_id")
    margen_monto = fields.Monetary(string="Margen del mes", currency_field="currency_id")
    margen_pct = fields.Float(string="Margen %", digits=(16, 2))
    facturas = fields.Integer(string="Facturas")
    devoluciones = fields.Monetary(string="Devoluciones", currency_field="currency_id")

    # --- acumulado 12 meses móviles al corte ---
    venta_12m = fields.Monetary(string="Venta 12M", currency_field="currency_id")
    margen_12m = fields.Monetary(string="Margen 12M", currency_field="currency_id")
    margen_pct_12m = fields.Float(string="Margen % 12M", digits=(16, 2))

    # --- evaluación al corte ---
    score = fields.Float(string="Score", digits=(16, 1))
    grado = fields.Selection(
        [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D"), ("nuevo", "Nuevo")],
        string="Grado")
    tendencia = fields.Selection(
        [("subida_fuerte", "▲▲ Crecimiento fuerte"),
         ("subida", "▲ Mejora"),
         ("estable", "► Estable"),
         ("baja", "▼ Deterioro"),
         ("baja_fuerte", "▼▼ Deterioro grave"),
         ("divergente", "⚠ Divergente")],
        string="Tendencia")
    cxc_total = fields.Monetary(string="CxC al corte", currency_field="currency_id")
    cxc_vencido = fields.Monetary(string="Vencido al corte", currency_field="currency_id")
    dias_cobro = fields.Float(string="Días de cobro", digits=(16, 1))

    segmento = fields.Char(string="Segmento al corte")
    user_id = fields.Many2one("res.users", string="Vendedor al corte")
    currency_id = fields.Many2one(
        "res.currency", string="Moneda",
        default=lambda self: self.env.company.currency_id)

    _sql_constraints = [
        ("snapshot_unico",
         "unique(partner_id, fecha_corte, company_id)",
         "Ya existe un snapshot de este cliente para ese corte."),
    ]

    @api.depends("partner_id", "periodo")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "%s · %s" % (
                rec.partner_id.display_name or "", rec.periodo or "")
