# -*- coding: utf-8 -*-
from odoo import models, fields, api


class AgsMercado(models.Model):
    """Segmentacion comercial limpia.

    ORIGEN: al analizar las categorias de cliente de Odoo aparecieron 44
    combinaciones distintas para 1,744 facturas, porque las etiquetas mezclan
    tres ejes independientes: geografia (Santo Domingo, Santiago), importancia
    (Normal, Importante, Muy Importante) y canal comercial (Almacenista,
    Distribuidor, Supermercado, Horeca). Como se combinan libremente,
    "Almacenista" aparece en seis variantes y ninguna agrupacion es sumable.

    Este modelo separa el eje que importa para gestion: el canal. Un cliente
    pertenece a un mercado y solo a uno, de modo que las metas de margen y
    los analisis de rentabilidad por canal son agregables sin ambiguedad.

    No reemplaza las categorias de Odoo, que siguen sirviendo para geografia
    e importancia. Las complementa.
    """
    _name = "ags.mercado"
    _description = "AG Intelligence - Mercado / Canal Comercial"
    _order = "secuencia, name"

    name = fields.Char(string="Mercado", required=True)
    codigo = fields.Char(string="Codigo", required=True)
    secuencia = fields.Integer(string="Secuencia", default=10)
    descripcion = fields.Text(string="Descripcion")

    tipo = fields.Selection(
        [
            ("mayorista", "Mayorista / Distribuidor"),
            ("almacenista", "Almacenista"),
            ("supermercado", "Supermercado / Cadena"),
            ("horeca", "Hotelero y restaurantes"),
            ("institucional", "Institucional"),
            ("detalle", "Detalle / Ruta"),
            ("exportacion", "Exportacion"),
            ("otro", "Otro"),
        ],
        string="Tipo de canal",
        required=True,
    )
    plazo_esperado = fields.Integer(
        string="Plazo de pago esperado (dias)",
        help="Plazo normal del canal. El sector hotelero opera con 45 a 60 "
             "dias, los almacenes y supermercados con 30. Sin esta referencia "
             "un DSO consolidado no dice nada: 45 dias es excelente en un "
             "canal y pesimo en otro.",
    )
    margen_objetivo = fields.Float(
        string="Margen objetivo (%)",
        digits=(5, 2),
        help="Margen bruto esperado del canal. Sirve de referencia por "
             "defecto al crear metas.",
    )

    categoria_ids = fields.Many2many(
        "res.partner.category",
        "ags_mercado_categoria_rel",
        "mercado_id",
        "categoria_id",
        string="Etiquetas equivalentes",
        help="Etiquetas de cliente de Odoo que corresponden a este mercado. "
             "Sirve para asignar clientes en lote sin reclasificar a mano.",
    )
    partner_ids = fields.One2many("res.partner", "ags_mercado_id", string="Clientes")
    partner_count = fields.Integer(
        string="Clientes", compute="_compute_partner_count"
    )
    notas = fields.Text(string="Notas")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("codigo_unico", "unique(codigo)", "El codigo del mercado debe ser unico."),
    ]

    @api.depends("partner_ids")
    def _compute_partner_count(self):
        for rec in self:
            rec.partner_count = len(rec.partner_ids)

    def action_asignar_por_etiqueta(self):
        """Asigna a este mercado los clientes que tienen las etiquetas mapeadas.

        No sobreescribe asignaciones existentes: si un cliente ya tiene
        mercado, se respeta. La asignacion manual siempre gana sobre la
        deduccion por etiqueta.
        """
        self.ensure_one()
        if not self.categoria_ids:
            return False
        candidatos = self.env["res.partner"].search([
            ("category_id", "in", self.categoria_ids.ids),
            ("ags_mercado_id", "=", False),
            ("customer_rank", ">", 0),
        ])
        candidatos.write({"ags_mercado_id": self.id})
        return len(candidatos)


class ResPartnerMercado(models.Model):
    _inherit = "res.partner"

    ags_mercado_id = fields.Many2one(
        "ags.mercado",
        string="Mercado",
        index=True,
        help="Canal comercial al que pertenece el cliente. Un cliente "
             "pertenece a un solo mercado, de modo que los analisis por canal "
             "son agregables sin ambiguedad.",
    )
