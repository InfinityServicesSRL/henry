# -*- coding: utf-8 -*-
from odoo import models, fields, api


class AgsMedicion(models.Model):
    """Serie temporal de valores reales calculados desde Odoo.

    Cada medicion se compara contra dos referencias distintas y ambas importan:
      - contra el BASELINE: mejore o empeore respecto a donde arranque?
      - contra el BENCHMARK: donde estoy parado frente al mercado?

    Una empresa puede estar mejorando fuerte y aun asi seguir en rojo contra
    el mercado, o al reves. Mostrar solo una de las dos comparaciones da una
    lectura incompleta.
    """
    _name = "ags.medicion"
    _description = "AG Intelligence - Medicion"
    _order = "fecha_periodo desc, parametro_id"

    parametro_id = fields.Many2one(
        "ags.parametro", string="Parametro", required=True, ondelete="cascade"
    )
    codigo_parametro = fields.Char(
        related="parametro_id.codigo", string="Codigo", store=True
    )
    seccion = fields.Selection(related="parametro_id.seccion", string="Seccion", store=True)
    unidad = fields.Selection(related="parametro_id.unidad", string="Unidad")
    direccion = fields.Selection(related="parametro_id.direccion", string="Direccion")

    valor = fields.Float(string="Valor", digits=(16, 2), required=True)
    fecha_periodo = fields.Date(
        string="Periodo",
        required=True,
        help="Fecha representativa del periodo medido. Para mensual, usar el "
             "ultimo dia del mes.",
    )
    anio = fields.Integer(string="Año", compute="_compute_periodo", store=True)
    mes = fields.Integer(string="Mes", compute="_compute_periodo", store=True)

    origen = fields.Selection(
        [
            ("auto", "Calculo automatico"),
            ("manual", "Carga manual"),
            ("externa", "Fuente externa"),
        ],
        string="Origen",
        default="auto",
        required=True,
    )
    fecha_calculo = fields.Datetime(
        string="Calculado el", default=fields.Datetime.now, readonly=True
    )

    semaforo = fields.Selection(
        [
            ("verde", "Verde"),
            ("amarillo", "Amarillo"),
            ("rojo", "Rojo"),
            ("sin_dato", "Sin dato"),
        ],
        string="Semaforo vs mercado",
        compute="_compute_comparaciones",
        store=True,
    )
    variacion_baseline = fields.Float(
        string="Variacion vs baseline (%)",
        compute="_compute_comparaciones",
        store=True,
        digits=(16, 2),
    )
    tendencia_baseline = fields.Selection(
        [
            ("mejora", "Mejorando"),
            ("estable", "Estable"),
            ("deterioro", "Deteriorando"),
            ("sin_dato", "Sin baseline"),
        ],
        string="Tendencia vs baseline",
        compute="_compute_comparaciones",
        store=True,
    )
    brecha_objetivo = fields.Float(
        string="Brecha al objetivo",
        compute="_compute_comparaciones",
        store=True,
        digits=(16, 2),
        help="Cuanto falta para alcanzar el objetivo del benchmark vigente",
    )
    valor_esperado_estacional = fields.Float(
        string="Valor esperado por estacionalidad",
        compute="_compute_estacional",
        digits=(16, 2),
        help="Solo aplica en parametros marcados con estacionalidad",
    )
    desvio_estacional = fields.Float(
        string="Desvio vs esperado (%)",
        compute="_compute_estacional",
        digits=(16, 2),
    )
    notas = fields.Text(string="Notas")

    _sql_constraints = [
        (
            "medicion_unica",
            "unique(parametro_id, fecha_periodo)",
            "Ya existe una medicion de este parametro para ese periodo.",
        ),
    ]

    @api.depends("fecha_periodo")
    def _compute_periodo(self):
        for rec in self:
            if rec.fecha_periodo:
                rec.anio = rec.fecha_periodo.year
                rec.mes = rec.fecha_periodo.month
            else:
                rec.anio = 0
                rec.mes = 0

    @api.depends("valor", "parametro_id")
    def _compute_comparaciones(self):
        for rec in self:
            param = rec.parametro_id
            rec.semaforo = param.evaluar(rec.valor) if param else "sin_dato"

            baseline = param.baseline_vigente_id if param else False
            if baseline and baseline.valor:
                variacion = ((rec.valor - baseline.valor) / abs(baseline.valor)) * 100.0
                rec.variacion_baseline = variacion
                umbral = 2.0
                if abs(variacion) < umbral:
                    rec.tendencia_baseline = "estable"
                elif param.direccion == "menor_mejor":
                    rec.tendencia_baseline = "mejora" if variacion < 0 else "deterioro"
                elif param.direccion == "mayor_mejor":
                    rec.tendencia_baseline = "mejora" if variacion > 0 else "deterioro"
                else:
                    rec.tendencia_baseline = "sin_dato"
            else:
                rec.variacion_baseline = 0.0
                rec.tendencia_baseline = "sin_dato"

            benchmark = param.benchmark_vigente_id if param else False
            if benchmark:
                rec.brecha_objetivo = benchmark.valor_objetivo - rec.valor
            else:
                rec.brecha_objetivo = 0.0

    @api.depends("valor", "mes", "parametro_id")
    def _compute_estacional(self):
        for rec in self:
            rec.valor_esperado_estacional = 0.0
            rec.desvio_estacional = 0.0
            param = rec.parametro_id
            if not param or not param.aplica_estacionalidad or not rec.mes:
                continue
            factor = param.estacionalidad_ids.filtered(lambda f: f.mes == str(rec.mes))[:1]
            baseline = param.baseline_vigente_id
            if factor and baseline and baseline.valor:
                esperado = baseline.valor * factor.factor
                rec.valor_esperado_estacional = esperado
                if esperado:
                    rec.desvio_estacional = ((rec.valor - esperado) / abs(esperado)) * 100.0

    @api.model
    def cron_calcular_mediciones(self):
        """Punto de entrada del calculo periodico.

        Cada parametro declara en metodo_tecnico el nombre del metodo que lo
        calcula. Los calculadores se implementan por fase; los parametros sin
        metodo se saltan en silencio en lugar de romper la corrida completa.
        """
        parametros = self.env["ags.parametro"].search([
            ("captura", "=", "auto"),
            ("metodo_tecnico", "!=", False),
        ])
        calculador = self.env["ags.calculador"]
        for param in parametros:
            metodo = getattr(calculador, param.metodo_tecnico, None)
            if not metodo:
                continue
            try:
                metodo(param)
            except Exception:
                self.env.cr.rollback()
                continue
        return True
