# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AgsBaseline(models.Model):
    """Fotografia congelada del punto de partida.

    REGLA CENTRAL DEL DISENO: un baseline congelado NO se modifica nunca.
    Si el baseline se recalculara con cada consulta, se perderia la capacidad
    de medir mejora, que es justamente el proposito del sistema.

    Cuando se limpian los datos del ERP se crea una version nueva (v2) y la
    anterior queda visible. La distancia entre v1 y v2 revela cuanto estaba
    distorsionando la data sucia, y ese numero es en si mismo un hallazgo.
    """
    _name = "ags.baseline"
    _description = "AG Intelligence - Baseline Congelado"
    _order = "parametro_id, version desc"

    parametro_id = fields.Many2one(
        "ags.parametro", string="Parametro", required=True, ondelete="cascade"
    )
    codigo_parametro = fields.Char(
        related="parametro_id.codigo", string="Codigo", store=True
    )
    unidad = fields.Selection(related="parametro_id.unidad", string="Unidad")

    valor = fields.Float(string="Valor congelado", digits=(16, 2), required=True)
    periodo_desde = fields.Date(string="Periodo desde", required=True)
    periodo_hasta = fields.Date(string="Periodo hasta", required=True)

    version = fields.Integer(string="Version", default=1, required=True)
    vigente = fields.Boolean(
        string="Es el vigente",
        default=True,
        help="Solo una version por parametro debe estar marcada como vigente",
    )
    congelado = fields.Boolean(
        string="Congelado",
        default=False,
        readonly=True,
        help="Una vez congelado el registro no admite cambios",
    )
    fecha_congelamiento = fields.Datetime(string="Fecha de congelamiento", readonly=True)
    congelado_por_id = fields.Many2one(
        "res.users", string="Congelado por", readonly=True
    )

    metodo_captura = fields.Selection(
        [
            ("auto", "Automatica desde Odoo"),
            ("manual", "Registro manual"),
            ("externa", "Fuente externa"),
        ],
        string="Metodo de captura",
        required=True,
        default="auto",
    )
    calidad_dato = fields.Selection(
        [
            ("verificado", "Verificado - datos limpios"),
            ("parcial", "Parcial - limpieza pendiente"),
            ("sucio", "Sin verificar - dato crudo"),
        ],
        string="Calidad del dato",
        required=True,
        default="sucio",
        help="Ser honesto aqui es lo que permite interpretar el baseline "
             "correctamente mas adelante.",
    )
    notas = fields.Text(string="Notas de calidad")

    variacion_vs_baseline = fields.Float(
        string="Ultima variacion vs baseline (%)",
        compute="_compute_variacion",
        digits=(16, 2),
    )

    @api.depends("valor", "parametro_id.ultima_medicion_id")
    def _compute_variacion(self):
        for rec in self:
            medicion = rec.parametro_id.ultima_medicion_id
            if medicion and rec.valor:
                rec.variacion_vs_baseline = (
                    (medicion.valor - rec.valor) / abs(rec.valor)
                ) * 100.0
            else:
                rec.variacion_vs_baseline = 0.0

    def action_congelar(self):
        """Congela el baseline. Accion irreversible por diseno."""
        for rec in self:
            if rec.congelado:
                raise UserError(_("Este baseline ya esta congelado."))
            rec.write({
                "congelado": True,
                "fecha_congelamiento": fields.Datetime.now(),
                "congelado_por_id": self.env.user.id,
            })
        return True

    def action_nueva_version(self):
        """Crea una version nueva del baseline dejando la anterior visible."""
        self.ensure_one()
        nueva = self.copy({
            "version": self.version + 1,
            "vigente": True,
            "congelado": False,
            "fecha_congelamiento": False,
            "congelado_por_id": False,
        })
        self.vigente = False
        return {
            "type": "ir.actions.act_window",
            "res_model": "ags.baseline",
            "res_id": nueva.id,
            "view_mode": "form",
            "target": "current",
        }

    def write(self, vals):
        """Bloquea cambios en registros congelados."""
        campos_permitidos = {"vigente", "notas"}
        for rec in self:
            if rec.congelado and not set(vals.keys()).issubset(campos_permitidos):
                raise UserError(
                    _("El baseline de %s esta congelado y no admite cambios. "
                      "Si necesita corregirlo, cree una version nueva.")
                    % rec.parametro_id.name
                )
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.congelado:
                raise UserError(
                    _("No se puede eliminar un baseline congelado. "
                      "Desmarque 'vigente' si ya no aplica.")
                )
        return super().unlink()

    # ------------------------------------------------------------------
    # Etapa 0 - Base de confianza
    # ------------------------------------------------------------------

    METODOS_CAPTURA = ("auto", "manual", "externa")

    @api.model
    def generar_baselines_iniciales(
        self, periodos=6, calidad="sucio", congelar=True, solo_faltantes=True
    ):
        """Crea el baseline inicial de cada parametro que tenga mediciones.

        Que se congela: el promedio de los PRIMEROS periodos no atipicos de la
        serie, no de los ultimos. El baseline es el punto de partida — de donde
        se venia — asi que tomar la cola de la serie lo convertiria en una foto
        del presente y anularia la comparacion.

        Calidad del dato: se marca 'sucio' por defecto y es deliberado. Estas
        mediciones salieron de un ERP sin depurar (asientos en borrador, OTs
        vencidas, inventario negativo). Declararlo permite mas adelante crear
        una v2 sobre datos limpios y leer la distancia entre v1 y v2 como lo
        que es: cuanto estaba distorsionando la data sucia.

        Idempotente: con solo_faltantes=True no toca parametros que ya tienen
        baseline vigente, asi que se puede correr las veces que haga falta.

        Devuelve un dict con el resumen de la corrida.
        """
        Param = self.env["ags.parametro"].with_context(active_test=False)
        Medicion = self.env["ags.medicion"]

        creados = []
        omitidos_con_baseline = 0
        omitidos_sin_medicion = 0

        for param in Param.search([]):
            if solo_faltantes and param.baseline_ids.filtered(lambda b: b.vigente):
                omitidos_con_baseline += 1
                continue

            mediciones = Medicion.search(
                [
                    ("parametro_id", "=", param.id),
                    ("periodo_atipico", "=", False),
                ],
                order="fecha_periodo asc",
                limit=periodos,
            )
            if not mediciones:
                omitidos_sin_medicion += 1
                continue

            valores = mediciones.mapped("valor")
            fechas = mediciones.mapped("fecha_periodo")
            captura = param.captura if param.captura in self.METODOS_CAPTURA else "auto"

            anteriores = self.search([("parametro_id", "=", param.id)])
            version = (max(anteriores.mapped("version")) + 1) if anteriores else 1
            if anteriores:
                anteriores.filtered(lambda b: b.vigente).write({"vigente": False})

            baseline = self.create({
                "parametro_id": param.id,
                "valor": sum(valores) / len(valores),
                "periodo_desde": min(fechas),
                "periodo_hasta": max(fechas),
                "version": version,
                "vigente": True,
                "metodo_captura": captura,
                "calidad_dato": calidad,
                "notas": (
                    "Generado por la Etapa 0 del cockpit. Promedio de los %s "
                    "primeros periodos no atipicos de la serie (%s a %s). "
                    "Calidad declarada '%s': las mediciones de origen no han "
                    "sido depuradas."
                ) % (len(valores), min(fechas), max(fechas), calidad),
            })
            if congelar:
                baseline.action_congelar()
            creados.append(baseline.id)

        resumen = {
            "creados": len(creados),
            "omitidos_con_baseline": omitidos_con_baseline,
            "omitidos_sin_medicion": omitidos_sin_medicion,
            "ids": creados,
        }
        _logger.info("ags.baseline.generar_baselines_iniciales: %s", resumen)
        return resumen

    @api.model
    def accion_congelar_y_recomputar(self):
        """Etapa 0 completa: congelar baselines y recomputar las mediciones.

        Es lo que corre la migracion, expuesto tambien como accion de servidor
        para poder relanzarlo desde la interfaz sin depender de un rebuild.
        """
        resumen = self.generar_baselines_iniciales()
        resumen["mediciones_recomputadas"] = (
            self.env["ags.medicion"].recomputar_comparaciones()
        )
        return resumen
