# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class AgsParametro(models.Model):
    """Catalogo maestro de variables medibles.

    Define QUE se mide. Es la tabla estable del sistema: cambia poco.
    Los valores viven en ags.baseline (congelado), ags.benchmark (externo)
    y ags.medicion (serie real desde Odoo).
    """
    _name = "ags.parametro"
    _description = "AG Intelligence - Parametro de Gestion"
    _order = "seccion, secuencia, codigo"

    codigo = fields.Char(
        string="Codigo",
        required=True,
        help="Identificador tecnico estable. Ej: MARGEN_BRUTO. "
             "No cambiar despues de congelar un baseline.",
    )
    name = fields.Char(string="Parametro", required=True)
    seccion = fields.Selection(
        [
            ("salud_erp", "Salud del ERP"),
            ("cockpit", "Cockpit de Gerencia"),
            ("demanda", "Demanda y Ventas"),
            ("inventario", "Inventario y Materiales"),
            ("costos", "Costos y Rentabilidad"),
            ("financiero", "Financiero y Caja"),
            ("comercial", "Inteligencia Comercial"),
            ("rrhh", "Personal y Nomina"),
            ("macro", "Contexto Macroeconomico"),
        ],
        string="Seccion",
        required=True,
    )
    secuencia = fields.Integer(string="Secuencia", default=10)
    descripcion = fields.Text(
        string="Que mide",
        help="Definicion en lenguaje llano de que representa este numero",
    )
    unidad = fields.Selection(
        [
            ("pct", "Porcentaje (%)"),
            ("dias", "Dias"),
            ("dop", "Pesos dominicanos (RD$)"),
            ("usd", "Dolares (US$)"),
            ("cantidad", "Cantidad / conteo"),
            ("ratio", "Ratio / indice"),
            ("kwh_ton", "kWh por tonelada"),
        ],
        string="Unidad",
        required=True,
    )
    direccion = fields.Selection(
        [
            ("mayor_mejor", "Mayor es mejor"),
            ("menor_mejor", "Menor es mejor"),
            ("neutro", "Neutro / solo contexto"),
        ],
        string="Direccion deseada",
        required=True,
        default="mayor_mejor",
        help="Determina como se calcula el semaforo. Neutro es para variables "
             "de contexto como tipo de cambio, que no son metas.",
    )
    tipo_benchmark = fields.Selection(
        [
            ("a", "A - Dato duro dominicano verificable"),
            ("b", "B - Rango de industria global adaptable"),
            ("c", "C - Sin referencia publica, usar historico propio"),
        ],
        string="Tipo de benchmark",
        required=True,
        default="c",
        help="Clasifica la calidad de la referencia externa disponible. "
             "Los tipo C solo pueden compararse contra el propio baseline.",
    )
    metodo_calculo = fields.Text(
        string="Formula / metodo",
        help="Como se obtiene el valor desde los datos de Odoo. "
             "Documentar modelos y campos usados.",
    )
    metodo_tecnico = fields.Char(
        string="Metodo tecnico",
        help="Nombre del metodo Python que calcula este parametro. "
             "Ej: _calc_margen_bruto. Se implementan por fase.",
    )
    captura = fields.Selection(
        [
            ("auto", "Automatica desde Odoo"),
            ("manual", "Registro manual"),
            ("externa", "Fuente externa"),
        ],
        string="Tipo de captura",
        default="auto",
        required=True,
        help="Critico para OEE: la captura manual sobreestima 8-12 puntos "
             "frente a la medicion automatica.",
    )
    frecuencia = fields.Selection(
        [
            ("diaria", "Diaria"),
            ("semanal", "Semanal"),
            ("quincenal", "Quincenal"),
            ("mensual", "Mensual"),
            ("trimestral", "Trimestral"),
            ("anual", "Anual"),
        ],
        string="Frecuencia",
        default="mensual",
        required=True,
    )
    responsable_id = fields.Many2one(
        "res.users",
        string="Responsable",
        help="Quien responde por este indicador y recibe sus alertas",
    )
    aplica_estacionalidad = fields.Boolean(
        string="Aplica estacionalidad",
        default=False,
        help="Marcar en parametros de demanda y ventas, donde el mes del año "
             "afecta el valor esperado.",
    )
    notas = fields.Text(string="Notas y salvedades")

    baseline_ids = fields.One2many("ags.baseline", "parametro_id", string="Baselines")
    benchmark_ids = fields.One2many("ags.benchmark", "parametro_id", string="Benchmarks")
    medicion_ids = fields.One2many("ags.medicion", "parametro_id", string="Mediciones")
    estacionalidad_ids = fields.One2many(
        "ags.estacionalidad", "parametro_id", string="Factores de estacionalidad"
    )

    baseline_vigente_id = fields.Many2one(
        "ags.baseline",
        string="Baseline vigente",
        compute="_compute_vigentes",
        store=False,
    )
    benchmark_vigente_id = fields.Many2one(
        "ags.benchmark",
        string="Benchmark vigente",
        compute="_compute_vigentes",
        store=False,
    )
    ultima_medicion_id = fields.Many2one(
        "ags.medicion",
        string="Ultima medicion",
        compute="_compute_vigentes",
        store=False,
    )
    valor_actual = fields.Float(
        string="Valor actual",
        compute="_compute_vigentes",
        digits=(16, 2),
        store=False,
    )
    semaforo = fields.Selection(
        [
            ("verde", "Verde"),
            ("amarillo", "Amarillo"),
            ("rojo", "Rojo"),
            ("sin_evidencia", "Sin evidencia"),
            ("sin_dato", "Sin dato"),
        ],
        string="Semaforo",
        compute="_compute_vigentes",
        store=False,
    )
    madurez = fields.Selection(
        [
            ("no_medible", "No medible"),
            ("con_reservas", "Medible con reservas"),
            ("confiable", "Confiable"),
        ],
        string="Madurez del dato",
        compute="_compute_madurez",
        store=True,
        help="Distingue entre no tener dato y tener un dato que todavia no "
             "significa nada. Un indicador con pocos periodos de operacion "
             "estable puede mostrar un numero y ser puro ruido.",
    )
    madurez_detalle = fields.Char(
        string="Por que",
        compute="_compute_madurez",
        store=True,
    )
    periodos_validos = fields.Integer(
        string="Periodos utiles",
        compute="_compute_madurez",
        store=True,
        help="Mediciones dentro del regimen vigente y no marcadas como atipicas",
    )
    requiere_config = fields.Boolean(
        string="Requiere configuracion",
        default=False,
        help="Marcar en parametros que no calculan hasta que se declaren "
             "cuentas, categorias o proveedores en la configuracion contable.",
    )
    config_faltante = fields.Char(
        string="Configuracion pendiente",
        help="Que falta declarar para que este parametro pueda calcularse",
    )
    hallazgo_ids = fields.Many2many(
        "ags.hallazgo",
        string="Hallazgos que lo tocan",
        compute="_compute_confiabilidad",
        help="Hallazgos vivos de reglas que declaran a este indicador entre "
             "los que invalidan o degradan.",
    )
    confiabilidad = fields.Selection(
        [
            ("ok", "Sin hallazgos"),
            ("con_reserva", "Leer con reserva"),
            ("invalidado", "No confiable"),
        ],
        string="Confiabilidad",
        compute="_compute_confiabilidad",
        help="Si el REGISTRO que alimenta este numero esta sano. No sustituye "
             "a la madurez: aquella responde si llevo suficientes periodos "
             "midiendo, esta responde si lo que mido esta bien registrado. Un "
             "indicador puede tener 18 periodos limpios y estar invalidado "
             "porque su categoria de producto lleva meses sin generar asiento "
             "de costo de ventas.",
    )
    confiabilidad_detalle = fields.Char(
        string="Por que no es confiable",
        compute="_compute_confiabilidad",
    )
    confidencial = fields.Boolean(
        string="Confidencial",
        default=False,
        help="Marca los indicadores que solo debe ver Gerencia: margenes por "
             "cliente, P&L, cuentas por pagar, costos unitarios. La "
             "clasificacion vive aqui y no en el codigo, para que reclasificar "
             "un indicador sea editar un registro y no desplegar una version.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("codigo_unico", "unique(codigo)", "El codigo del parametro debe ser unico."),
    ]

    @api.constrains("codigo")
    def _check_codigo(self):
        for rec in self:
            if rec.codigo and (" " in rec.codigo or not rec.codigo.isupper()):
                raise ValidationError(
                    _("El codigo debe ir en mayusculas y sin espacios. Ej: MARGEN_BRUTO")
                )

    @api.depends("baseline_ids", "benchmark_ids", "medicion_ids")
    def _compute_vigentes(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            baseline = rec.baseline_ids.filtered(lambda b: b.vigente)[:1]
            benchmark = rec.benchmark_ids.filtered(
                lambda b: b.vigente_desde <= hoy
                and (not b.vigente_hasta or b.vigente_hasta >= hoy)
            )[:1]
            medicion = rec.medicion_ids.sorted("fecha_periodo", reverse=True)[:1]

            rec.baseline_vigente_id = baseline.id if baseline else False
            rec.benchmark_vigente_id = benchmark.id if benchmark else False
            rec.ultima_medicion_id = medicion.id if medicion else False
            rec.valor_actual = medicion.valor if medicion else 0.0
            rec.semaforo = medicion.semaforo if medicion else "sin_dato"

    @api.depends("medicion_ids", "medicion_ids.periodo_atipico", "requiere_config")
    def _compute_madurez(self):
        """Clasifica la confiabilidad del indicador, no su valor.

        Un parametro puede mostrar un numero y aun asi no ser interpretable:
        si el proceso que lo alimenta cambio hace tres meses, la serie mezcla
        dos realidades distintas. Distinguirlo evita que alguien tome una
        decision sobre ruido creyendo que son datos.
        """
        Regimen = self.env["ags.regimen"]
        for rec in self:
            mediciones = rec.medicion_ids
            if not mediciones:
                rec.madurez = "no_medible"
                rec.periodos_validos = 0
                rec.madurez_detalle = (
                    rec.config_faltante or "Sin mediciones registradas"
                ) if rec.requiere_config else "Sin mediciones registradas"
                continue

            reg = Regimen.regimen_vigente(parametro=rec)
            utiles = mediciones.filtered(lambda m: not m.periodo_atipico)
            if reg and not reg.datos_previos_validos:
                utiles = utiles.filtered(
                    lambda m: m.fecha_periodo >= reg.fecha_inicio)
            n = len(utiles)
            rec.periodos_validos = n

            if not n:
                rec.madurez = "no_medible"
                rec.madurez_detalle = "Sin periodos utiles en el regimen vigente"
            elif reg and n < reg.meses_maduracion:
                rec.madurez = "con_reservas"
                rec.madurez_detalle = (
                    "%s de %s periodos desde: %s"
                    % (n, reg.meses_maduracion, reg.name)
                )
            elif n < 3:
                rec.madurez = "con_reservas"
                rec.madurez_detalle = "Solo %s periodo(s) medido(s)" % n
            else:
                rec.madurez = "confiable"
                rec.madurez_detalle = "%s periodos utiles" % n

    def _compute_confiabilidad(self):
        """Cruza los hallazgos vivos contra los indicadores que declaran tocar.

        NO se almacena. Un campo store que dependa del estado de los hallazgos
        quedaria desactualizado en cuanto el cron de auditoria cierre uno de
        madrugada, que es el mismo defecto que ya arrastra
        ags.benchmark.es_vigente. Son setenta parametros: calcularlo en cada
        lectura es barato y siempre es cierto.

        Se resuelve por busqueda y no declarando el many2many inverso de
        ags.regla.parametro_ids: el nombre de la tabla intermedia lo genera
        Odoo y depender de haberlo adivinado bien es fragil.
        """
        Hallazgo = self.env["ags.hallazgo"]
        vivos = Hallazgo.search([
            ("vivo", "=", True),
            ("regla_id.efecto", "in", ["invalida", "degrada"]),
        ])
        por_parametro = {}
        for h in vivos:
            for p in h.regla_id.parametro_ids:
                por_parametro.setdefault(p.id, []).append(h)

        for rec in self:
            suyos = por_parametro.get(rec.id, [])
            rec.hallazgo_ids = [(6, 0, [h.id for h in suyos])]
            if not suyos:
                rec.confiabilidad = "ok"
                rec.confiabilidad_detalle = False
                continue
            invalidan = [h for h in suyos if h.regla_id.efecto == "invalida"]
            rec.confiabilidad = "invalidado" if invalidan else "con_reserva"
            # Se citan los que mandan, no todos: una frase con nueve motivos
            # no se lee, y el expediente completo esta a un clic.
            citados = (invalidan or suyos)[:2]
            texto = " · ".join(h.sujeto or h.codigo_regla for h in citados)
            sobran = len(invalidan or suyos) - len(citados)
            if sobran > 0:
                texto += " (y %s mas)" % sobran
            rec.confiabilidad_detalle = texto

    def action_ver_hallazgos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Hallazgos que afectan a %s" % self.name,
            "res_model": "ags.hallazgo",
            "domain": [("id", "in", self.hallazgo_ids.ids)],
            "view_mode": "list,form",
            "target": "current",
        }

    @api.model
    def recalcular_madurez(self):
        """Fuerza el recalculo de la madurez en todos los parametros.

        Necesario tras crear o modificar un regimen de datos, porque el
        cambio no dispara la dependencia automaticamente.
        """
        todos = self.search([])
        todos._compute_madurez()
        return {e: len(todos.filtered(lambda p: p.madurez == e))
                for e in ("confiable", "con_reservas", "no_medible")}

    def name_get(self):
        return [(rec.id, "[%s] %s" % (rec.codigo, rec.name)) for rec in self]

    def evaluar(self, valor):
        """Devuelve el semaforo de un valor contra el benchmark vigente.

        Respeta la direccion del parametro: en 'menor_mejor' las bandas se
        invierten. Los parametros neutros nunca generan semaforo.
        """
        self.ensure_one()
        if self.direccion == "neutro":
            return "sin_dato"
        bm = self.benchmark_vigente_id
        if not bm:
            return "sin_dato"
        return bm.evaluar_valor(valor)
