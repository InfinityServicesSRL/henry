# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


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
    # Almacenado a proposito: la regla de registro que filtra lo confidencial
    # se evalua en SQL, y un related sin store obligaria a resolverlo fila a
    # fila en Python sobre las 317 mediciones en cada lectura.
    confidencial = fields.Boolean(
        related="parametro_id.confidencial", string="Confidencial", store=True)
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
            ("sin_evidencia", "Sin evidencia"),
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
    valor_baseline = fields.Float(
        string="Baseline de referencia",
        compute="_compute_comparaciones",
        store=True,
        digits=(16, 2),
        help="Valor congelado contra el que se midio esta comparacion. Se "
             "guarda para que el cockpit no tenga que resolverlo de nuevo y "
             "para poder auditar contra que se comparo.",
    )
    valor_objetivo = fields.Float(
        string="Objetivo de referencia",
        compute="_compute_comparaciones",
        store=True,
        digits=(16, 2),
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
    evidencia_n = fields.Integer(
        string="Registros de respaldo",
        default=0,
        help="Cuantos registros base sustentan este numero. No es el valor: "
             "es el conteo de la prueba. Un calculador que no lo declara "
             "deja el campo en cero y sin_evidencia en falso, que era el "
             "comportamiento anterior.",
    )
    sin_evidencia = fields.Boolean(
        string="Sin evidencia",
        default=False,
        help="El calculador declaro evidencia y encontro cero registros. El "
             "valor calculado es cero por construccion, no por resultado.",
    )

    periodo_atipico = fields.Boolean(
        string="Periodo atipico",
        default=False,
        help="Marca periodos distorsionados por eventos no operativos: cierre "
             "fiscal, ajuste de inventario, reclasificacion contable. Los "
             "periodos marcados se excluyen de promedios y del calculo de "
             "estacionalidad, pero se conservan visibles.",
    )
    motivo_atipico = fields.Char(
        string="Motivo",
        help="Por que este periodo no refleja la operacion normal",
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

    @api.depends(
        "valor",
        "sin_evidencia",
        "parametro_id",
        "parametro_id.direccion",
        "parametro_id.baseline_ids",
        "parametro_id.baseline_ids.valor",
        "parametro_id.baseline_ids.vigente",
        "parametro_id.benchmark_ids",
        "parametro_id.benchmark_ids.valor_minimo",
        "parametro_id.benchmark_ids.valor_objetivo",
        "parametro_id.benchmark_ids.vigente_desde",
        "parametro_id.benchmark_ids.vigente_hasta",
    )
    def _compute_comparaciones(self):
        """Compara cada medicion contra baseline y benchmark.

        El baseline y el benchmark se resuelven aqui directamente sobre los
        one2many del parametro, en lugar de leer parametro_id.baseline_vigente_id
        y parametro_id.benchmark_vigente_id. Dos razones:

        1. Aquellos son computes no almacenados que dependen de medicion_ids y
           leen medicion.semaforo. Como semaforo es este mismo compute, la
           cadena se muerde la cola: medicion.semaforo -> parametro._compute_vigentes
           -> medicion.semaforo. Resolviendolo local se corta la recursion.
        2. Un @api.depends solo puede seguir campos almacenados o relaciones
           reales. Declarar la dependencia sobre baseline_ids/benchmark_ids es
           lo que hace que congelar un baseline recalcule las mediciones ya
           guardadas, que era exactamente lo que no ocurria antes.
        """
        hoy = fields.Date.context_today(self)
        for rec in self:
            param = rec.parametro_id

            baseline = False
            benchmark = False
            if param:
                baseline = param.baseline_ids.filtered(lambda b: b.vigente)[:1]
                benchmark = param.benchmark_ids.filtered(
                    lambda b: b.vigente_desde
                    and b.vigente_desde <= hoy
                    and (not b.vigente_hasta or b.vigente_hasta >= hoy)
                )[:1]

            # D14: un cero sin evidencia no es un buen resultado, es un
            # registro que no se hizo. Se decide ANTES que todo lo demas,
            # incluida la direccion del parametro: la ausencia de registro
            # importa igual en un indicador neutro, y un benchmark que
            # evalue ese cero lo pintaria verde.
            if rec.sin_evidencia:
                rec.semaforo = "sin_evidencia"
            elif not param or param.direccion == "neutro" or not benchmark:
                rec.semaforo = "sin_dato"
            else:
                rec.semaforo = benchmark.evaluar_valor(rec.valor)

            if baseline and baseline.valor:
                rec.valor_baseline = baseline.valor
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
                rec.valor_baseline = 0.0
                rec.variacion_baseline = 0.0
                rec.tendencia_baseline = "sin_dato"

            if benchmark:
                rec.valor_objetivo = benchmark.valor_objetivo
                rec.brecha_objetivo = benchmark.valor_objetivo - rec.valor
            else:
                rec.valor_objetivo = 0.0
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
            baseline = param.baseline_ids.filtered(lambda b: b.vigente)[:1]
            if factor and baseline and baseline.valor:
                esperado = baseline.valor * factor.factor
                rec.valor_esperado_estacional = esperado
                if esperado:
                    rec.desvio_estacional = ((rec.valor - esperado) / abs(esperado)) * 100.0

    CAMPOS_COMPARACION = (
        "semaforo",
        "variacion_baseline",
        "tendencia_baseline",
        "brecha_objetivo",
        "valor_baseline",
        "valor_objetivo",
    )

    @api.model
    def recomputar_comparaciones(self, dominio=None, tam_lote=500):
        """Fuerza el recomputo de las comparaciones ya almacenadas.

        Ampliar el @api.depends solo afecta a los cambios futuros: las
        mediciones que ya estan en base quedan con el valor que se calculo
        cuando no habia baselines. Este metodo es idempotente y se puede
        correr las veces que haga falta.
        """
        registros = self.with_context(active_test=False).search(dominio or [])
        campos = [self._fields[n] for n in self.CAMPOS_COMPARACION]
        total = len(registros)
        for inicio in range(0, total, tam_lote):
            lote = registros[inicio:inicio + tam_lote]
            for campo in campos:
                self.env.add_to_compute(campo, lote)
            lote.flush_recordset(list(self.CAMPOS_COMPARACION))
        _logger.info("ags.medicion: recomputadas %s mediciones", total)
        return total

    @api.model
    def promedio_limpio(self, parametro, meses=6, hasta=None):
        """Promedio del parametro excluyendo periodos atipicos.

        Un cierre fiscal o un ajuste de inventario puede mover un indicador
        20 puntos en un mes. Si esos periodos entran al promedio, la linea
        base queda desviada y la estacionalidad que se derive de ella tambien.
        """
        hasta = hasta or fields.Date.context_today(self)
        registros = self.search([
            ("parametro_id", "=", parametro.id),
            ("fecha_periodo", "<=", hasta),
            ("periodo_atipico", "=", False),
        ], order="fecha_periodo desc", limit=meses)
        if not registros:
            return 0.0, 0
        valores = registros.mapped("valor")
        return sum(valores) / len(valores), len(valores)

    @api.model
    def cron_calcular_mediciones(self):
        """Punto de entrada del calculo periodico.

        Cada parametro declara en metodo_tecnico el nombre del metodo que lo
        calcula. Los calculadores se implementan por fase; los parametros sin
        metodo se saltan en silencio en lugar de romper la corrida completa.

        Cada calculo corre dentro de su propio savepoint. Un cr.rollback()
        plano revertia la transaccion entera, incluidos los parametros que ya
        habian calculado bien antes del que fallo.
        """
        parametros = self.env["ags.parametro"].search([
            ("captura", "=", "auto"),
            ("metodo_tecnico", "!=", False),
        ])
        calculador = self.env["ags.calculador"]
        fallidos = 0
        for param in parametros:
            metodo = getattr(calculador, param.metodo_tecnico, None)
            if not metodo:
                continue
            try:
                with self.env.cr.savepoint():
                    metodo(param)
            except Exception:
                fallidos += 1
                _logger.exception(
                    "ags.medicion: fallo el calculo de %s (%s)",
                    param.codigo, param.metodo_tecnico,
                )
                continue
        if fallidos:
            _logger.warning(
                "ags.medicion: %s de %s parametros fallaron en la corrida",
                fallidos, len(parametros),
            )
        return True
