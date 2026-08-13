# -*- coding: utf-8 -*-
from odoo import models, fields, api


class AgsFuente(models.Model):
    """Registro de fuentes de investigacion.

    Cada valor de benchmark debe poder rastrearse hasta una fuente con fecha.
    Esto permite que investigaciones futuras se incorporen de forma ordenada
    y que se pueda auditar de donde salio cada numero.
    """
    _name = "ags.fuente"
    _description = "AG Intelligence - Fuente de Investigacion"
    _order = "fecha_consulta desc, name"

    name = fields.Char(
        string="Fuente",
        required=True,
        help="Nombre de la institucion o publicacion. Ej: Superintendencia de Bancos RD",
    )
    documento = fields.Char(
        string="Documento",
        help="Titulo especifico del informe o pagina consultada",
    )
    url = fields.Char(string="URL")
    fecha_publicacion = fields.Date(
        string="Fecha del dato",
        help="Fecha a la que corresponde el dato, no la fecha de consulta",
    )
    fecha_consulta = fields.Date(
        string="Fecha de consulta",
        default=fields.Date.context_today,
        required=True,
    )
    tipo = fields.Selection(
        [
            ("oficial_rd", "Oficial dominicana"),
            ("gremial_rd", "Gremial dominicana"),
            ("financiero", "Estado financiero de empresa"),
            ("industria", "Publicacion de industria"),
            ("indice", "Indice de precios"),
            ("interno", "Analisis interno AG Supply"),
        ],
        string="Tipo de fuente",
        required=True,
    )
    confiabilidad = fields.Selection(
        [
            ("alta", "Alta"),
            ("media", "Media"),
            ("baja", "Baja"),
        ],
        string="Confiabilidad",
        default="media",
        required=True,
    )
    notas = fields.Text(
        string="Notas",
        help="Salvedades de interpretacion. Ej: cifra de fabricante integrado, "
             "no directamente comparable con convertidor.",
    )
    benchmark_ids = fields.One2many(
        "ags.benchmark", "fuente_id", string="Benchmarks derivados"
    )
    benchmark_count = fields.Integer(
        string="Benchmarks", compute="_compute_benchmark_count"
    )
    active = fields.Boolean(default=True)

    @api.depends("benchmark_ids")
    def _compute_benchmark_count(self):
        for rec in self:
            rec.benchmark_count = len(rec.benchmark_ids)
