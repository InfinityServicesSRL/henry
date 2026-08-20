# -*- coding: utf-8 -*-
import logging

from odoo import models, api, fields

_logger = logging.getLogger(__name__)


class AgsCalculadorCalidad(models.AbstractModel):
    """Indicadores de calidad del registro.

    El resto del modulo mide COMO VA el negocio. Esta capa mide si el ERP
    esta en condiciones de responder esa pregunta, y lo hace sobre las
    practicas concretas que, cuando se incumplen, producen los descuadres
    que ya se documentaron: cuentas puente que no cierran, inventario en
    negativo, conciliacion bancaria pendiente y costos de produccion
    incompletos.

    La diferencia con la seccion de Salud del ERP existente es el sujeto:
    aquella cuenta pendientes acumulados (asientos en borrador, OTs
    vencidas), esta mide si la CONFIGURACION y el HABITO que los previenen
    estan en su sitio. Un pendiente se resuelve trabajando; una
    configuracion incorrecta vuelve a generar pendientes indefinidamente.
    """
    _inherit = "ags.calculador"

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    @api.model
    def _cuentas_transitorias_tesoreria(self):
        """Cuentas donde Odoo deja los cobros y pagos sin conciliar.

        Se descubren desde la configuracion en lugar de fijarlas por codigo:
        cada metodo de pago declara su cuenta pendiente y cada diario su
        cuenta transitoria. Asi el indicador sigue funcionando si manana se
        agrega un banco o se separan las cuentas por diario, que es
        justamente una de las mejoras recomendadas.
        """
        cuentas = self.env["account.account"].browse()
        lineas = self.env["account.payment.method.line"].search([])
        for linea in lineas:
            if linea.payment_account_id:
                cuentas |= linea.payment_account_id
        diarios = self.env["account.journal"].search([
            ("type", "in", ["bank", "cash"]),
        ])
        for diario in diarios:
            if diario.suspense_account_id:
                cuentas |= diario.suspense_account_id
        return cuentas

    @api.model
    def _cuentas_entrada_mercancia(self):
        """Cuenta puente de bienes recibidos no facturados.

        Sale de las categorias de producto, que es donde vive la
        configuracion real, no de un codigo contable escrito en el codigo.
        """
        categorias = self.env["product.category"].search([])
        return categorias.mapped(
            "property_stock_account_input_categ_id").filtered(lambda a: a)

    # ------------------------------------------------------------------
    # Compras
    # ------------------------------------------------------------------

    @api.model
    def _calc_control_recepcion(self, parametro, fecha=None):
        """% de productos comprables facturados contra cantidad recibida.

        Con la politica en 'cantidades pedidas' el sistema permite facturar
        lo que aun no ha llegado y la conciliacion a tres bandas ni siquiera
        se activa. Es la palanca que evita que la cuenta de bienes recibidos
        no facturados siga creciendo.
        """
        _, ultimo = self._rango_mes(fecha)
        Producto = self.env["product.template"]
        dominio = [("purchase_ok", "=", True)]
        total = Producto.search_count(dominio)
        if not total:
            return False
        controlados = Producto.search_count(
            dominio + [("purchase_method", "=", "receive")])
        nota = "Con control por recepcion: %s de %s productos comprables" % (
            controlados, total)
        return self._registrar(
            parametro, controlados / total * 100.0, ultimo, notas=nota)

    @api.model
    def _calc_antiguedad_grni(self, parametro, fecha=None):
        """Antiguedad promedio del saldo de bienes recibidos no facturados.

        Un saldo alto puede ser normal si es reciente: son facturas que
        todavia no han llegado. Lo que delata un circuito roto es la
        antiguedad. Se pondera por importe para que una partida pequena y
        muy vieja no distorsione la lectura.
        """
        _, ultimo = self._rango_mes(fecha)
        cuentas = self._cuentas_entrada_mercancia()
        if not cuentas:
            return False
        lineas = self.env["account.move.line"].search([
            ("account_id", "in", cuentas.ids),
            ("parent_state", "=", "posted"),
            ("date", "<=", ultimo),
        ])
        peso = dias = 0.0
        for linea in lineas:
            importe = abs(linea.balance)
            if not importe:
                continue
            peso += importe
            dias += importe * (ultimo - linea.date).days
        if not peso:
            return False
        nota = "Ponderado sobre %s lineas por RD$ %s" % (
            len(lineas), round(peso, 2))
        return self._registrar(parametro, dias / peso, ultimo, notas=nota)

    # ------------------------------------------------------------------
    # Inventario y contabilidad
    # ------------------------------------------------------------------

    @api.model
    def _calc_cuentas_inventario_acreedoras(self, parametro, fecha=None):
        """Cuentas de inventario con saldo acreedor.

        Un inventario en negativo implica haber dado salida a existencias
        que nunca ingresaron. No admite tolerancia: cualquier cantidad
        distinta de cero invalida el costo del periodo.
        """
        _, ultimo = self._rango_mes(fecha)
        cuentas = self._config().cuenta_inventario_ids
        if not cuentas:
            return False
        afectadas = []
        for cuenta in cuentas:
            if self._saldo_balance(cuenta, ultimo) < 0:
                afectadas.append(cuenta.code or str(cuenta.id))
        nota = ("Cuentas afectadas: %s" % ", ".join(afectadas)) if afectadas \
            else "Todas las cuentas de inventario con saldo deudor"
        return self._registrar(parametro, len(afectadas), ultimo, notas=nota)

    @api.model
    def _calc_bancos_acreedores(self, parametro, fecha=None):
        """Cuentas de banco y efectivo con saldo acreedor."""
        _, ultimo = self._rango_mes(fecha)
        cuentas = self.env["account.account"].search([
            ("account_type", "in", ["asset_cash", "liability_credit_card"]),
        ])
        afectadas = []
        for cuenta in cuentas:
            if self._saldo_balance(cuenta, ultimo) < 0:
                afectadas.append(cuenta.code or str(cuenta.id))
        nota = ("Cuentas afectadas: %s" % ", ".join(afectadas)) if afectadas \
            else "Ninguna cuenta de tesoreria con saldo acreedor"
        return self._registrar(parametro, len(afectadas), ultimo, notas=nota)

    @api.model
    def _calc_tesoreria_sin_conciliar(self, parametro, fecha=None):
        """Importe retenido en cuentas transitorias de tesoreria.

        Cobros y pagos registrados que nunca se contrastaron contra el
        extracto bancario. Se mide el importe en valor absoluto: recibos y
        pagos pendientes tienen signo opuesto y sumarlos con su signo
        escondería el problema por compensacion.
        """
        _, ultimo = self._rango_mes(fecha)
        cuentas = self._cuentas_transitorias_tesoreria()
        if not cuentas:
            return False
        total = 0.0
        detalle = []
        for cuenta in cuentas:
            saldo = self._saldo_balance(cuenta, ultimo)
            if saldo:
                total += abs(saldo)
                detalle.append("%s: %s" % (cuenta.code, round(saldo, 2)))
        nota = " | ".join(detalle) if detalle else "Sin saldo pendiente"
        return self._registrar(parametro, total, ultimo, notas=nota)

    @api.model
    def _calc_transferencias_abiertas(self, parametro, fecha=None):
        """Saldo de la cuenta puente de traspasos entre cuentas propias.

        Debe quedar en cero despues de cada traspaso: todo lo que sale de
        una cuenta entra a otra. Un saldo distinto de cero significa que hay
        traspasos con un solo extremo registrado, o registrados en direccion
        equivocada.
        """
        _, ultimo = self._rango_mes(fecha)
        cuenta = getattr(self.env.company, "transfer_account_id", False)
        if not cuenta:
            return False
        saldo = self._saldo_balance(cuenta, ultimo)
        nota = "Cuenta %s. El saldo esperado es cero." % (cuenta.code or "")
        return self._registrar(parametro, abs(saldo), ultimo, notas=nota)

    # ------------------------------------------------------------------
    # Manufactura
    # ------------------------------------------------------------------

    @api.model
    def _calc_centros_con_costo(self, parametro, fecha=None):
        """% de centros de trabajo con costo por hora configurado.

        Sin costo por hora, el costo de una orden de produccion solo incluye
        materiales y toda la mano de obra de conversion queda fuera. El
        margen resultante se ve mejor de lo que es.
        """
        _, ultimo = self._rango_mes(fecha)
        Centro = self.env["mrp.workcenter"]
        total = Centro.search_count([])
        if not total:
            return False
        con_costo = Centro.search_count([("costs_hour", ">", 0)])
        nota = "Con costo por hora: %s de %s centros de trabajo" % (
            con_costo, total)
        return self._registrar(
            parametro, con_costo / total * 100.0, ultimo, notas=nota)

    @api.model
    def _calc_bom_con_tolerancia(self, parametro, fecha=None):
        """% de listas de materiales con control de consumo activo.

        Con el consumo libre, un operador puede consumir de mas sin dejar
        constancia y la merma termina diluida en el costo del producto en
        lugar de aparecer como una partida analizable. Se cuentan como
        controladas las listas configuradas con aviso o con bloqueo.
        """
        _, ultimo = self._rango_mes(fecha)
        Bom = self.env["mrp.bom"]
        total = Bom.search_count([])
        if not total:
            return False
        controladas = Bom.search_count([
            ("consumption", "in", ["warning", "strict"]),
        ])
        nota = "Con control de consumo: %s de %s listas de materiales" % (
            controladas, total)
        return self._registrar(
            parametro, controladas / total * 100.0, ultimo, notas=nota)
