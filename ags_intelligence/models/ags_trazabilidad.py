# -*- coding: utf-8 -*-
import logging
import threading

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Almacen por hilo. Cada corrida del cron o cada peticion web vive en su
# propio hilo, de modo que las trazas de dos calculos simultaneos no se
# mezclan. Se usa un almacen y no el contexto de Odoo porque el contexto es
# inmutable y tendria que propagarse a mano por los 54 calculadores.
_ALMACEN = threading.local()

# Si un calculo acumula mas piezas que esto, algo no consumio su traza y se
# descarta antes que atribuirle a un indicador componentes que no son suyos.
TOPE_COMPONENTES = 40


def _traza():
    if not hasattr(_ALMACEN, "componentes"):
        _ALMACEN.componentes = []
    return _ALMACEN.componentes


class AgsCalculadorTraza(models.AbstractModel):
    """Instrumentacion de los calculos para hacerlos auditables.

    En lugar de modificar los 54 calculadores uno por uno, se interceptan
    las dos funciones por las que pasa todo saldo contable del modulo. Cada
    vez que un calculo consulta cuentas, queda anotado que cuentas fueron,
    con que rango de fechas y que importe devolvieron. Al registrar la
    medicion, esas anotaciones se guardan como componentes.

    El efecto es que cualquier indicador contable, presente o futuro, nace
    con su desglose sin que su autor tenga que hacer nada.
    """
    _inherit = "ags.calculador"

    # ------------------------------------------------------------------
    # Almacen de traza
    # ------------------------------------------------------------------

    @api.model
    def _limpiar_traza(self):
        _traza().clear()

    @api.model
    def _anotar(self, rol, valor, tipo="contable", cuentas=None,
                desde=None, hasta=None, modelo=None, dominio=None, notas=None):
        """Registra una pieza del calculo en curso."""
        _traza().append({
            "rol": rol,
            "tipo": tipo,
            "valor": valor,
            "cuenta_ids": cuentas.ids if cuentas else [],
            "fecha_desde": desde,
            "fecha_hasta": hasta,
            "modelo": modelo,
            "dominio": repr(dominio) if dominio else False,
            "notas": notas,
        })
        return valor

    @api.model
    def _rotulo(self, cuentas):
        """Nombre legible de un conjunto de cuentas.

        Cuando el calculador no declara un concepto, se usa el nombre de la
        cuenta. Los nombres del plan contable de AG Supply son descriptivos,
        asi que el desglose se lee igual de bien.
        """
        if not cuentas:
            return "Sin cuentas"
        if len(cuentas) == 1:
            return "%s %s" % (cuentas.code or "", cuentas.name or "")
        codigos = ", ".join(sorted(c.code or "" for c in cuentas)[:4])
        if len(cuentas) > 4:
            codigos += " y %s mas" % (len(cuentas) - 4)
        return "%s cuentas: %s" % (len(cuentas), codigos)

    # ------------------------------------------------------------------
    # Interceptacion de los dos accesos a saldos
    # ------------------------------------------------------------------

    @api.model
    def _saldo_cuentas(self, cuentas, desde, hasta, invertir=False, rol=None):
        total = super()._saldo_cuentas(cuentas, desde, hasta, invertir=invertir)
        self._anotar(
            rol or self._rotulo(cuentas), total,
            cuentas=cuentas, desde=desde, hasta=hasta,
            notas="Movimiento del periodo" + (" (signo invertido)" if invertir else ""),
        )
        return total

    @api.model
    def _saldo_balance(self, cuentas, hasta, invertir=False, rol=None):
        total = super()._saldo_balance(cuentas, hasta, invertir=invertir)
        self._anotar(
            rol or self._rotulo(cuentas), total,
            cuentas=cuentas, hasta=hasta,
            notas="Saldo acumulado hasta la fecha" + (" (signo invertido)" if invertir else ""),
        )
        return total

    @api.model
    def _contar(self, modelo, dominio, rol=None):
        """Cuenta registros dejando constancia del filtro usado.

        Los calculadores que cuentan documentos —ordenes vencidas, asientos
        en borrador— pueden usar este metodo en lugar de search_count para
        que su cifra tambien quede explicada.
        """
        cantidad = self.env[modelo].search_count(dominio)
        self._anotar(
            rol or modelo, cantidad, tipo="conteo",
            modelo=modelo, dominio=dominio,
        )
        return cantidad

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    @api.model
    def _registrar(self, parametro, valor, fecha_periodo, origen="auto", notas=False):
        medicion = super()._registrar(
            parametro, valor, fecha_periodo, origen=origen, notas=notas)

        piezas = list(_traza())
        self._limpiar_traza()

        if not medicion:
            return medicion

        if len(piezas) > TOPE_COMPONENTES:
            _logger.warning(
                "ags.componente: %s acumulo %s piezas, se descarta la traza",
                parametro.codigo, len(piezas))
            piezas = []

        # El desglose se reemplaza completo en cada recalculo: conservar
        # componentes de una corrida anterior junto a los nuevos daria un
        # desglose que no suma.
        medicion.componente_ids.unlink()
        Componente = self.env["ags.componente"]
        for indice, pieza in enumerate(piezas):
            Componente.create({
                "medicion_id": medicion.id,
                "secuencia": (indice + 1) * 10,
                "rol": pieza["rol"],
                "tipo": pieza["tipo"],
                "valor": pieza["valor"],
                "cuenta_ids": [(6, 0, pieza["cuenta_ids"])],
                "fecha_desde": pieza["fecha_desde"],
                "fecha_hasta": pieza["fecha_hasta"],
                "modelo": pieza["modelo"],
                "dominio": pieza["dominio"],
                "notas": pieza["notas"],
            })
        return medicion

    @api.model
    def calcular_periodo(self, fecha=None, codigos=None):
        """Limpia la traza antes de la corrida completa."""
        self._limpiar_traza()
        return super().calcular_periodo(fecha=fecha, codigos=codigos)


class AgsMedicionComponentes(models.Model):
    _inherit = "ags.medicion"

    componente_ids = fields.One2many(
        "ags.componente", "medicion_id", string="Desglose del calculo")
    n_componentes = fields.Integer(
        string="Piezas", compute="_compute_n_componentes")

    def _compute_n_componentes(self):
        for rec in self:
            rec.n_componentes = len(rec.componente_ids)

    def action_ver_componentes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Desglose de %s" % (self.parametro_id.name or ""),
            "res_model": "ags.componente",
            "domain": [("medicion_id", "=", self.id)],
            "view_mode": "list,form",
            "target": "current",
        }
