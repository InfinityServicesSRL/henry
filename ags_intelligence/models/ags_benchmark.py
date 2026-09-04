# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class AgsBenchmark(models.Model):
    """Valores de referencia externos, versionados.

    Un benchmark nunca se sobreescribe: cuando llega investigacion nueva se
    crea un registro nuevo y el anterior recibe fecha de cierre. Asi se
    conserva el historial de contra que se estaba midiendo en cada momento.

    Se guardan bandas, no numeros unicos, porque un solo valor no distingue
    entre 'aceptable' y 'excelente'.
    """
    _name = "ags.benchmark"
    _description = "AG Intelligence - Benchmark de Referencia"
    _order = "parametro_id, vigente_desde desc"

    parametro_id = fields.Many2one(
        "ags.parametro", string="Parametro", required=True, ondelete="cascade"
    )
    codigo_parametro = fields.Char(
        related="parametro_id.codigo", string="Codigo", store=True
    )
    unidad = fields.Selection(related="parametro_id.unidad", string="Unidad")
    direccion = fields.Selection(related="parametro_id.direccion", string="Direccion")
    tipo_benchmark = fields.Selection(
        related="parametro_id.tipo_benchmark", string="Tipo"
    )

    valor_minimo = fields.Float(
        string="Minimo aceptable",
        digits=(16, 2),
        help="Por debajo de esto (o por encima, si menor es mejor) hay problema",
    )
    valor_objetivo = fields.Float(
        string="Objetivo",
        digits=(16, 2),
        required=True,
        help="La meta realista para AG Supply en el horizonte de planeacion",
    )
    valor_clase_mundial = fields.Float(
        string="Clase mundial",
        digits=(16, 2),
        help="Referencia de mejor practica. Aspiracional, no meta inmediata.",
    )

    fuente_id = fields.Many2one("ags.fuente", string="Fuente")
    fuente_detalle = fields.Char(
        string="Cita especifica",
        help="Pagina, tabla o seccion exacta de donde sale el numero",
    )
    ajuste_aplicado = fields.Text(
        string="Ajuste aplicado",
        help="Si el dato viene de un fabricante integrado y se ajusto a la "
             "realidad de un convertidor, documentar el razonamiento aqui.",
    )

    vigente_desde = fields.Date(
        string="Vigente desde",
        required=True,
        default=fields.Date.context_today,
    )
    vigente_hasta = fields.Date(
        string="Vigente hasta",
        help="Se llena automaticamente al crear una version posterior",
    )
    version = fields.Integer(string="Version", default=1, readonly=True)
    es_vigente = fields.Boolean(
        string="Vigente", compute="_compute_es_vigente", store=True
    )
    notas = fields.Text(string="Notas")

    @api.depends("vigente_desde", "vigente_hasta")
    def _compute_es_vigente(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            rec.es_vigente = bool(
                rec.vigente_desde
                and rec.vigente_desde <= hoy
                and (not rec.vigente_hasta or rec.vigente_hasta >= hoy)
            )

    @api.constrains("valor_minimo", "valor_objetivo", "valor_clase_mundial")
    def _check_bandas(self):
        """Las bandas deben ser coherentes con la direccion del parametro."""
        for rec in self:
            if not rec.parametro_id or rec.parametro_id.direccion == "neutro":
                continue
            vals = [rec.valor_minimo, rec.valor_objetivo, rec.valor_clase_mundial]
            if not all(v is not None for v in vals):
                continue
            if rec.parametro_id.direccion == "mayor_mejor":
                if not (rec.valor_minimo <= rec.valor_objetivo <= rec.valor_clase_mundial):
                    raise ValidationError(
                        _("En un parametro donde mayor es mejor, las bandas deben "
                          "ordenarse: minimo <= objetivo <= clase mundial.")
                    )
            else:
                if not (rec.valor_minimo >= rec.valor_objetivo >= rec.valor_clase_mundial):
                    raise ValidationError(
                        _("En un parametro donde menor es mejor, las bandas deben "
                          "ordenarse: minimo >= objetivo >= clase mundial.")
                    )

    @api.model_create_multi
    def create(self, vals_list):
        """Al crear una version nueva, cierra la anterior automaticamente."""
        registros = super().create(vals_list)
        for rec in registros:
            anteriores = self.search([
                ("parametro_id", "=", rec.parametro_id.id),
                ("id", "!=", rec.id),
                ("vigente_hasta", "=", False),
            ])
            if anteriores:
                rec.version = max(anteriores.mapped("version")) + 1
                anteriores.write({
                    "vigente_hasta": fields.Date.subtract(rec.vigente_desde, days=1)
                })
        return registros

    def evaluar_valor(self, valor):
        """Clasifica un valor en verde / amarillo / rojo segun las bandas."""
        self.ensure_one()
        if self.direccion == "menor_mejor":
            if valor <= self.valor_objetivo:
                return "verde"
            if valor <= self.valor_minimo:
                return "amarillo"
            return "rojo"
        if valor >= self.valor_objetivo:
            return "verde"
        if valor >= self.valor_minimo:
            return "amarillo"
        return "rojo"

    def name_get(self):
        return [
            (rec.id, "%s v%s (%s)" % (
                rec.parametro_id.codigo or "", rec.version, rec.vigente_desde or ""
            ))
            for rec in self
        ]
