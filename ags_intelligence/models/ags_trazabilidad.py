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


# Nombre que debe llevar cada conjunto de cuentas cuando aparezca en el
# desglose. Se resuelve comparando el conjunto que recibio el calculo contra
# los conjuntos que define la configuracion: asi ningun calculador tiene que
# declarar su etiqueta a mano, y los que se escriban manana la heredan.
_ETIQUETAS = [
    ("cuenta_inventario_ids", "Inventario"),
    ("cuenta_deuda_financiera_ids", "Deuda financiera"),
    ("cuenta_gasto_financiero_ids", "Gastos financieros"),
    ("cuentas_ingreso", "Ingresos operativos"),
    ("cuentas_ingreso_no_operativo", "Ingresos no operativos"),
    ("cuentas_costo_venta", "Costo de ventas"),
    ("cuentas_mod", "Mano de obra directa"),
    ("cuentas_gasto_operativo", "Gastos operativos"),
    ("cuentas_depreciacion", "Depreciacion"),
    ("cuentas_efectivo", "Efectivo y equivalentes"),
    ("cuentas_activo_circulante", "Activo circulante"),
    ("cuentas_activo_total", "Activo total"),
    ("cuentas_pasivo_corriente", "Pasivo corriente"),
    ("cuentas_pasivo_total", "Pasivo total"),
    ("cuentas_patrimonio", "Patrimonio"),
]

# Conjuntos que no vienen de un metodo con nombre sino de un filtro por tipo
# de cuenta dentro del propio calculo.
_ETIQUETAS_POR_TIPO = [
    (("income", "income_other"), "Ingresos del ejercicio"),
    (("expense", "expense_direct_cost", "expense_depreciation"),
     "Gastos del ejercicio"),
]


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
    def _catalogo(self):
        """Conjuntos de cuentas conocidos, con su nombre de negocio.

        Se arma una sola vez por corrida. Resolver la etiqueta comparando
        conjuntos, y no exigiendo que cada calculador la declare, significa
        que los 54 calculadores existentes quedan etiquetados sin tocarlos.
        """
        catalogo = getattr(_ALMACEN, "catalogo", None)
        if catalogo is not None:
            return catalogo
        catalogo = []
        try:
            cfg = self._config()
            try:
                _ALMACEN.lang = cfg._idioma_contable()
            except Exception:
                _ALMACEN.lang = False
            for nombre, etiqueta in _ETIQUETAS:
                try:
                    valor = getattr(cfg, nombre, None)
                    cuentas = valor() if callable(valor) else valor
                except Exception:
                    continue
                if cuentas:
                    catalogo.append((frozenset(cuentas.ids), etiqueta))
            for tipos, etiqueta in _ETIQUETAS_POR_TIPO:
                try:
                    cuentas = cfg._cuentas_por_tipo(list(tipos))
                except Exception:
                    continue
                if cuentas:
                    catalogo.append((frozenset(cuentas.ids), etiqueta))
        except Exception:
            _logger.warning(
                "ags.componente: no se pudo armar el catalogo de rotulos")
        _ALMACEN.catalogo = catalogo
        return catalogo

    @api.model
    def _rotulo(self, cuentas):
        """Nombre legible de un conjunto de cuentas.

        Primero se busca el conjunto en el catalogo: si el calculo consulto
        exactamente las cuentas de inventario, la pieza se llama
        "Inventario" y no "3 cuentas: 11050100...". Si el conjunto no
        corresponde a ninguno conocido se cae al nombre de la cuenta, y como
        ultimo recurso al conteo. El detalle de codigos vive en su propia
        columna; repetirlo aqui solo ensucia la lectura.
        """
        if not cuentas:
            return "Sin cuentas"
        ids = frozenset(cuentas.ids)
        for conjunto, etiqueta in self._catalogo():
            if conjunto == ids:
                return etiqueta
        if len(cuentas) == 1:
            cuenta = self._lang_contable(cuentas)
            return "%s %s" % (cuenta.code or "", cuenta.name or "")
        return "%s cuentas" % len(cuentas)

    @api.model
    def _lang_contable(self, cuentas):
        """Las cuentas leidas en el idioma en que AG Supply las nombro.

        El plan contable esta en espanol y las traducciones al ingles no son
        fieles: 11050200 es "Inventario de Materia Prima" en es_DO y
        "Allowance for doubtful accounts" en en_US. Un desglose que muestre
        el nombre en ingles no solo se lee raro, induce a error al auditor.
        """
        lang = getattr(_ALMACEN, "lang", None)
        if lang is None:
            self._catalogo()
            lang = getattr(_ALMACEN, "lang", False)
        return cuentas.with_context(lang=lang) if lang else cuentas

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

        # La pasada de saneado recalcula el mismo indicador con cuentas
        # excluidas. Guardar sus piezas junto a las de la corrida normal
        # daria un desglose con el doble de lineas que no suma al valor.
        if self.env.context.get("ags_sanear"):
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
        """Limpia la traza y el catalogo antes de la corrida completa."""
        self._limpiar_traza()
        _ALMACEN.catalogo = None
        _ALMACEN.lang = None
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
