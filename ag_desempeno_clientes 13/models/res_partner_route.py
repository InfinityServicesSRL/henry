# -*- coding: utf-8 -*-
"""Rutas con sentido geográfico.

El maestro de rutas actual mezcla tres cosas distintas bajo el mismo campo:
zonas reales ("SANTIAGO NORTE"), canales de venta ("OFICINA", 46 clientes,
"INTERNACIONAL") y hasta un nombre de vendedor ("Judith Compres"). Además
tiene duplicados por tildes y mayúsculas —"Tamboril/ canca" y "Tamboril/
Canca", "SANTO DOMINGO" contra "santo domingo este"— y 268 de 544 clientes
sin ruta asignada.

Con eso, agrupar la cartera por ruta no dice nada: la mitad cae en "sin
ruta" y la otra mitad mezcla geografía con canal.

Este modelo separa las tres cosas: la ruta es geografía (zona + provincia +
día de visita), el canal vive en su propio campo, y el vendedor sigue donde
siempre estuvo — en `user_id`. Un cliente de oficina en Santiago Norte
atendido por Gabriel tiene ahora las tres respuestas, no una sola casilla
peleada entre ellas.
"""

from odoo import api, fields, models


class ResPartnerRoute(models.Model):
    _inherit = "res.partner.route"
    _order = "zona, sequence, name"

    ZONAS = [
        ("santiago", "Santiago"),
        ("cibao", "Cibao"),
        ("norte", "Costa Norte"),
        ("sd", "Santo Domingo"),
        ("este", "Este"),
        ("sur", "Sur"),
        ("export", "Exportación"),
    ]

    zona = fields.Selection(
        ZONAS, string="Zona", index=True,
        help="Agrupador geográfico grande. La zona ordena el mapa; la ruta "
             "ordena el día de trabajo dentro de ella.")
    state_id = fields.Many2one(
        "res.country.state", string="Provincia",
        domain="[('country_id.code', '=', 'DO')]")
    km_desde_planta = fields.Float(
        string="Km desde planta", digits=(16, 1),
        help="Distancia típica de la ruta. Sirve de valor por defecto para "
             "los clientes nuevos que se asignen a ella.")
    costo_logistica_sugerido = fields.Selection(
        [("4", "4%"), ("3", "3%"), ("2", "2%")],
        string="Costo logístico sugerido")
    dia_visita = fields.Selection(
        [("1", "Lunes"), ("2", "Martes"), ("3", "Miércoles"),
         ("4", "Jueves"), ("5", "Viernes"), ("6", "Sábado")],
        string="Día de visita")
    frecuencia = fields.Selection(
        [("semanal", "Semanal"), ("quincenal", "Quincenal"),
         ("mensual", "Mensual"), ("demanda", "A demanda")],
        string="Frecuencia", default="semanal")
    user_id = fields.Many2one(
        "res.users", string="Vendedor habitual",
        help="Sólo referencia: la cartera se reparte por el vendedor de cada "
             "cliente, no por el de la ruta. Dos vendedores pueden compartir "
             "una zona.")
    partner_count = fields.Integer(
        string="Clientes", compute="_compute_partner_count")
    venta_12m = fields.Monetary(
        string="Venta 12M", compute="_compute_metricas",
        currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", compute="_compute_metricas")
    activa = fields.Boolean(string="Activa", default=True)

    def _compute_partner_count(self):
        datos = self.env["res.partner"]._read_group(
            [("ag_ruta", "in", self.ids), ("customer_rank", ">", 0)],
            groupby=["ag_ruta"], aggregates=["__count"])
        mapa = {ruta.id: n for ruta, n in datos}
        for r in self:
            r.partner_count = mapa.get(r.id, 0)

    def _compute_metricas(self):
        moneda = self.env.company.currency_id
        datos = self.env["res.partner"]._read_group(
            [("ag_ruta", "in", self.ids), ("customer_rank", ">", 0)],
            groupby=["ag_ruta"], aggregates=["ag_venta_12m:sum"])
        mapa = {ruta.id: total for ruta, total in datos}
        for r in self:
            r.currency_id = moneda
            r.venta_12m = mapa.get(r.id, 0.0)

    def action_ver_clientes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Clientes — %s" % self.display_name,
            "res_model": "res.partner",
            "view_mode": "list,kanban,form",
            "domain": [("ag_ruta", "=", self.id), ("customer_rank", ">", 0)],
            "context": {"default_ag_ruta": self.id},
        }
