# -*- coding: utf-8 -*-
import logging
import threading

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Que cuentas hay que dejar fuera del calculo en curso. Vive en el hilo y no
# en el contexto de Odoo por lo mismo que la traza: el contexto es inmutable y
# habria que propagarlo a mano por los 54 calculadores.
_CTX = threading.local()


class AgsCalculadorSaneado(models.AbstractModel):
    """Segunda lectura de los indicadores, con las cuentas ajustadas fuera.

    La corrida normal no cambia: cada indicador se calcula y se guarda con el
    saldo contable completo. Terminada esa corrida, los indicadores que tienen
    un ajuste vigente se vuelven a calcular una sola vez mas, esta vez con las
    cuentas excluidas, y el resultado se guarda al lado del crudo en la misma
    medicion.

    Se interceptan las dos mismas funciones que la trazabilidad, porque son
    las dos por las que pasa todo saldo contable del modulo. Un calculador que
    consulte cuentas por su cuenta no queda saneado, y eso es preferible a que
    quede saneado a medias sin que nadie lo note.

    LIMITACION CONOCIDA: los indicadores derivados de otras mediciones (CCC =
    DIO + DSO - DPO) leen el valor crudo tambien en la pasada saneada. Sanear
    en cadena exigiria un orden de dependencias que el modulo hoy no declara.
    """
    _inherit = "ags.calculador"

    # ------------------------------------------------------------------
    # Interceptacion
    # ------------------------------------------------------------------

    @api.model
    def _excluidas(self):
        if not self.env.context.get("ags_sanear"):
            return None
        return getattr(_CTX, "excluidas", None)

    @api.model
    def _saldo_cuentas(self, cuentas, desde, hasta, invertir=False, **kw):
        fuera = self._excluidas()
        if fuera and cuentas:
            cuentas = cuentas - fuera
        return super()._saldo_cuentas(
            cuentas, desde, hasta, invertir=invertir, **kw)

    @api.model
    def _saldo_balance(self, cuentas, hasta, invertir=False, **kw):
        fuera = self._excluidas()
        if fuera and cuentas:
            cuentas = cuentas - fuera
        return super()._saldo_balance(cuentas, hasta, invertir=invertir, **kw)

    # ------------------------------------------------------------------
    # Persistencia del valor saneado
    # ------------------------------------------------------------------

    @api.model
    def _registrar(self, parametro, valor, fecha_periodo, origen="auto",
                   notas=False, **kw):
        """En modo saneado no se crea medicion: se anota junto al crudo.

        Si la pasada normal no produjo medicion para este periodo, tampoco hay
        donde anotar. Devolver False en vez de crear una medicion suelta evita
        que aparezca un indicador que solo existe en su version ajustada.

        **kw en vez de la firma completa: esto es un envoltorio, no una
        reimplementacion. La evidencia, en particular, no se toca aqui -- el
        conteo de registros base es el mismo para las dos lecturas, porque un
        ajuste excluye cuentas del calculo, no hace aparecer ni desaparecer
        los registros que lo sustentan.
        """
        if not self.env.context.get("ags_sanear"):
            return super()._registrar(
                parametro, valor, fecha_periodo, origen=origen, notas=notas,
                **kw)

        # La traza de instrumentacion se acumulo durante este calculo y
        # aqui no se persiste: el desglose que se muestra es el de la corrida
        # contable. Si no se descarta, sus piezas se le atribuirian al
        # siguiente indicador.
        limpiar = getattr(self, "_limpiar_traza", None)
        if limpiar:
            limpiar()

        medicion = self.env["ags.medicion"].search([
            ("parametro_id", "=", parametro.id),
            ("fecha_periodo", "=", fecha_periodo),
        ], limit=1)
        if not medicion:
            return False
        ajustes = self.env["ags.ajuste"]._aplicables(parametro, fecha_periodo)
        medicion.write({
            "valor_saneado": valor,
            "ajuste_ids": [(6, 0, ajustes.ids)],
            "notas_saneado": notas or False,
        })
        return medicion

    # ------------------------------------------------------------------
    # Orquestacion
    # ------------------------------------------------------------------

    @api.model
    def calcular_periodo(self, fecha=None, codigos=None):
        resumen = super().calcular_periodo(fecha=fecha, codigos=codigos)
        try:
            saneados = self._calcular_saneados(fecha=fecha, codigos=codigos)
            if saneados:
                resumen = dict(resumen or {}, saneados=saneados)
        except Exception:
            # Un fallo saneando no puede tumbar la corrida normal: el valor
            # contable es el dato oficial y ya esta guardado.
            _logger.exception("Fallo la pasada de saneado")
        return resumen

    @api.model
    def _calcular_saneados(self, fecha=None, codigos=None):
        Ajuste = self.env["ags.ajuste"]
        _desde, hasta = self._rango_mes(fecha)

        # Limpieza previa: si un ajuste dejo de estar vigente, su saneado no
        # puede quedarse en pantalla como si siguiera aplicando.
        # Envuelto porque esta limpieza corre antes que los savepoints por
        # indicador y es el primer acceso a BD de la pasada.
        obsoletas = self.env["ags.medicion"].search([
            ("fecha_periodo", "=", hasta),
            ("ajuste_ids", "!=", False),
        ])
        if obsoletas:
            obsoletas.write({
                "valor_saneado": 0.0,
                "ajuste_ids": [(5, 0, 0)],
                "notas_saneado": False,
            })

        if not Ajuste._vigentes(hasta):
            return []

        afectados = Ajuste._parametros_afectados(hasta)
        if codigos:
            afectados = afectados.filtered(lambda p: p.codigo in codigos)

        entorno = self.with_context(ags_sanear=True)
        hechos = []
        for param in afectados:
            metodo = getattr(entorno, param.metodo_tecnico or "", None)
            if not metodo:
                continue
            _CTX.excluidas = Ajuste._cuentas_excluidas(param, hasta)
            if not _CTX.excluidas:
                continue
            try:
                # Savepoint por indicador: un calculador que falle saneado no
                # puede arrastrarse el resto de la pasada.
                with self.env.cr.savepoint():
                    if metodo(param, fecha):
                        hechos.append(param.codigo)
            except Exception:
                _logger.exception("Saneado de %s fallo", param.codigo)
            finally:
                _CTX.excluidas = None
        _logger.info("Saneado: %s indicadores recalculados", len(hechos))
        return hechos


class AgsMedicionSaneada(models.Model):
    _inherit = "ags.medicion"

    valor_saneado = fields.Float(
        string="Valor saneado", digits=(16, 4),
        help="El mismo indicador con las cuentas ajustadas fuera del calculo")
    notas_saneado = fields.Char(string="Detalle del saneado")
    ajuste_ids = fields.Many2many(
        "ags.ajuste", string="Ajustes aplicados")
    tiene_ajuste = fields.Boolean(
        string="Tiene ajuste", compute="_compute_tiene_ajuste", store=True)
    semaforo_saneado = fields.Selection(
        [
            ("verde", "Verde"),
            ("amarillo", "Amarillo"),
            ("rojo", "Rojo"),
            ("sin_dato", "Sin dato"),
        ],
        string="Semaforo saneado",
        compute="_compute_semaforo_saneado", store=True,
    )

    @api.depends("ajuste_ids")
    def _compute_tiene_ajuste(self):
        for rec in self:
            rec.tiene_ajuste = bool(rec.ajuste_ids)

    @api.depends("valor_saneado", "tiene_ajuste", "parametro_id",
                 "parametro_id.direccion", "parametro_id.benchmark_ids",
                 "parametro_id.benchmark_ids.valor_objetivo",
                 "parametro_id.benchmark_ids.valor_minimo",
                 "parametro_id.benchmark_ids.vigente_desde",
                 "parametro_id.benchmark_ids.vigente_hasta")
    def _compute_semaforo_saneado(self):
        """Mismo criterio que el semaforo normal, sobre el valor saneado.

        El benchmark se resuelve local, no a traves de benchmark_vigente_id,
        por la misma razon que en _compute_comparaciones: aquel es un compute
        no almacenado que lee las mediciones y la cadena se muerde la cola.
        """
        hoy = fields.Date.context_today(self)
        for rec in self:
            param = rec.parametro_id
            if not rec.tiene_ajuste or not param or param.direccion == "neutro":
                rec.semaforo_saneado = "sin_dato"
                continue
            benchmark = param.benchmark_ids.filtered(
                lambda b: b.vigente_desde
                and b.vigente_desde <= hoy
                and (not b.vigente_hasta or b.vigente_hasta >= hoy)
            )[:1]
            rec.semaforo_saneado = (
                benchmark.evaluar_valor(rec.valor_saneado)
                if benchmark else "sin_dato")
