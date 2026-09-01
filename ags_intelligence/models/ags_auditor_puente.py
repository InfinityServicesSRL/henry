# -*- coding: utf-8 -*-
import logging

from dateutil.relativedelta import relativedelta
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Tipos contables que no puede tener una cuenta puente: por definicion se
# liquida en dias o semanas, asi que nunca es no corriente ni de patrimonio.
TIPOS_PROHIBIDOS = ("asset_non_current", "asset_fixed", "liability_non_current",
                    "equity", "equity_unaffected")


class AgsAuditorPuente(models.AbstractModel):
    """Reglas sobre las cuentas puente.

    Cinco formas de fallar, y las cinco se ven distinto:

      1. La cuenta esta MAL TIPIFICADA -> su saldo sale del grupo contable
         que le corresponde y desaparece de las razones financieras.
      2. La cuenta NO ES CONCILIABLE -> sus partidas no pueden cruzarse
         nunca, asi que el circuito es imposible de cerrar por diseno.
      3. Hay PARTIDAS VIEJAS -> el circuito funciona pero algo se atasco.
      4. La cuenta NO CIERRA -> el saldo nunca cruza cero: solo acumula.
      5. La cuenta NO ESTA DECLARADA -> Odoo la usa como puente y el
         inventario no la conoce, asi que ninguna de las anteriores la mira.

    Las dos primeras son de configuracion y se arreglan una vez. La tercera y
    la cuarta son trabajo. La quinta es la que mantiene honesto al resto: sin
    ella, este bloque envejece solo y da falsos "todo limpio".
    """
    _inherit = "ags.auditor"

    # ------------------------------------------------------------------
    # Configuracion de la cuenta
    # ------------------------------------------------------------------

    @api.model
    def _regla_puente_no_corriente(self, regla, compania):
        """Cuentas puente tipificadas fuera del corto plazo.

        CASO REAL que motivo la regla: 21021200 Bienes Recibidos no
        Facturados acumula RD$ 174,448,990.91 acreedor y esta tipificada como
        liability_non_current. cuentas_pasivo_corriente() selecciona
        liability_payable y liability_current, de modo que esos 174 millones
        NO entran en el pasivo corriente y la razon corriente, la prueba
        acida y el capital de trabajo salen sobrestimados.

        Una cuenta puente se liquida en dias o semanas. Que sea no corriente
        es una contradiccion con su propio proposito.
        """
        salida = []
        puentes = self.env["ags.cuenta.puente"].search([
            ("activa", "=", True), ("company_id", "=", compania.id)])
        for p in puentes:
            cuenta = p.cuenta_id
            if cuenta.account_type not in TIPOS_PROHIBIDOS:
                continue
            nombre = self._cuentas_en_idioma(cuenta).name or cuenta.code
            salida.append({
                "clave": "%s:%s" % (regla.codigo, p.id),
                "sujeto": "%s %s es %s: su saldo queda fuera del corto plazo"
                          % (cuenta.code, nombre, cuenta.account_type),
                "cantidad": 1,
                "modelo": "account.account",
                "dominio": [("id", "=", cuenta.id)],
            })
        return salida

    @api.model
    def _regla_puente_no_conciliable(self, regla, compania):
        """Cuentas puente sin conciliacion permitida.

        Sin la marca de conciliable, las partidas de la cuenta no se pueden
        cruzar entre si. El circuito entonces no es que este atascado: es que
        no tiene forma de cerrarse, y cualquier medida de antiguedad sobre
        ella dara un falso negativo -- ninguna partida figura como saldada
        porque ninguna puede saldarse.

        Se comprueba antes que las reglas de antiguedad a proposito: es la
        precondicion que las hace significar algo.
        """
        salida = []
        puentes = self.env["ags.cuenta.puente"].search([
            ("activa", "=", True), ("company_id", "=", compania.id)])
        for p in puentes:
            if p.cuenta_id.reconcile:
                continue
            nombre = self._cuentas_en_idioma(p.cuenta_id).name or p.cuenta_id.code
            salida.append({
                "clave": "%s:%s" % (regla.codigo, p.id),
                "sujeto": "%s %s no permite conciliacion: sus partidas no "
                          "pueden cruzarse" % (p.cuenta_id.code, nombre),
                "cantidad": 1,
                "modelo": "account.account",
                "dominio": [("id", "=", p.cuenta_id.id)],
            })
        return salida

    @api.model
    def _regla_puente_sin_declarar(self, regla, compania):
        """Cuentas que Odoo usa como puente y el inventario no conoce.

        Es la regla que impide que este bloque envejezca. El dia que alguien
        agregue un banco, un metodo de pago o una categoria de producto con
        cuentas propias, la cuenta nueva aparece aqui en vez de quedarse
        invisible para las otras cuatro reglas.
        """
        Puente = self.env["ags.cuenta.puente"]
        candidatas, declaradas = Puente.cuentas_candidatas(compania)
        faltan = candidatas - declaradas.mapped("cuenta_id")
        if not faltan:
            return []
        nombres = ", ".join(
            "%s %s" % (c.code, self._cuentas_en_idioma(c).name or "")
            for c in faltan[:4])
        if len(faltan) > 4:
            nombres += " (y %s mas)" % (len(faltan) - 4)
        return [{
            "clave": "%s:%s" % (regla.codigo, compania.id),
            "sujeto": "%s cuentas puente sin declarar: %s" % (len(faltan), nombres),
            "cantidad": len(faltan),
            "modelo": "account.account",
            "dominio": [("id", "in", faltan.ids)],
        }]

    @api.model
    def _regla_puente_improvisado(self, regla, compania):
        """Cuentas que se usan como puente sin que nadie las haya declarado.

        LIMITE QUE ESTA REGLA CIERRA. El inventario se prepobla desde la
        configuracion de Odoo, y eso encuentra las cuentas que el SISTEMA usa
        como puente: metodos de pago, transitorias de diario, cuentas de
        categoria. No encuentra las que usa la GENTE, porque ninguna
        configuracion las referencia.

        Se descubrio de rebote analizando compras: 11050901 CUENTA PARA
        LIQUIDAR, con 110 lineas y 2,090,195.68 en 2026, y PUENTE_SIN_DECLARAR
        devolvia cero -- correctamente, porque nada en la configuracion apunta
        a ella. El nombre es el unico rastro que deja una puente improvisada,
        y por eso esta regla busca por nombre.

        Solo senala cuentas con movimiento: una cuenta que se llama
        transitoria y nunca se uso es ruido del plan contable, no un hallazgo.
        """
        cfg = self.env["ags.config"].get_config(compania)
        marcadores = [m.strip().upper()
                      for m in (cfg.marcadores_puente or "").split(",")
                      if m.strip()]
        if not marcadores:
            return []

        declaradas = self.env["ags.cuenta.puente"].search([
            ("company_id", "=", compania.id)]).mapped("cuenta_id").ids

        # Se acumulan IDS y no recordsets: unir dos recordsets que llevan
        # contextos distintos (uno con lang forzado, otro sin el) es fragil.
        ids = []
        for cuenta in self._cuentas_en_idioma(
                self.env["account.account"].search(
                    [("company_ids", "in", compania.id)])):
            if cuenta.id in declaradas:
                continue
            nombre = (cuenta.name or "").upper()
            if any(m in nombre for m in marcadores):
                ids.append(cuenta.id)
        if not ids:
            return []
        sospechosas = self.env["account.account"].browse(ids)

        # Solo las que se movieron: el plan contable de cualquier empresa
        # tiene cuentas con nombres asi que nadie usa nunca.
        grupos = self.env["account.move.line"]._read_group(
            [("account_id", "in", sospechosas.ids),
             ("parent_state", "=", "posted"),
             ("company_id", "=", compania.id)],
            ["account_id"], ["balance:sum", "__count"])

        salida = []
        for cuenta, balance, cantidad in grupos:
            if not cantidad:
                continue
            nombre = self._cuentas_en_idioma(cuenta).name or cuenta.code
            salida.append({
                "clave": "%s:%s" % (regla.codigo, cuenta.id),
                "sujeto": "%s %s se usa como puente sin estar declarada: "
                          "%s apuntes, saldo %s" % (
                              cuenta.code, nombre, cantidad,
                              "{:,.2f}".format(balance or 0.0)),
                "cantidad": cantidad,
                "modelo": "account.move.line",
                "dominio": [("account_id", "=", cuenta.id),
                            ("parent_state", "=", "posted"),
                            ("company_id", "=", compania.id)],
            })
        return salida

    # ------------------------------------------------------------------
    # Comportamiento de la cuenta
    # ------------------------------------------------------------------

    @api.model
    def _regla_puente_partidas_viejas(self, regla, compania):
        """Partidas sin conciliar por encima de la tolerancia de su proceso.

        Es la regla accionable del bloque: no devuelve un indicador, devuelve
        una lista de apuntes que alguien puede abrir y trabajar. Cada cuenta
        se juzga contra SU tolerancia, porque una transferencia entre bancos
        propios que lleva quince dias abierta es otra cosa que una recepcion
        de mercancia esperando su factura.
        """
        hoy = fields.Date.context_today(self)
        Linea = self.env["account.move.line"]
        salida = []
        for p in self.env["ags.cuenta.puente"].search([
                ("activa", "=", True), ("company_id", "=", compania.id)]):
            if not p.cuenta_id.reconcile:
                # Sin conciliacion no hay partida "abierta" que signifique
                # algo: lo denuncia PUENTE_NO_CONCILIABLE, no esta regla.
                continue
            corte = hoy - relativedelta(days=p.dias_tolerancia)
            dominio = [
                ("account_id", "=", p.cuenta_id.id),
                ("parent_state", "=", "posted"),
                ("full_reconcile_id", "=", False),
                ("date", "<=", corte),
                ("company_id", "=", compania.id),
            ]
            n = Linea.search_count(dominio)
            if not n:
                continue
            nombre = self._cuentas_en_idioma(p.cuenta_id).name or p.cuenta_id.code
            salida.append({
                "clave": "%s:%s" % (regla.codigo, p.id),
                "sujeto": "%s %s: %s partidas sin conciliar de mas de %s dias"
                          % (p.cuenta_id.code, nombre, n, p.dias_tolerancia),
                "cantidad": n,
                "modelo": "account.move.line",
                "dominio": dominio,
            })
        return salida

    @api.model
    def _regla_puente_no_cierra(self, regla, compania):
        """Cuentas puente cuyo saldo nunca cruza cero.

        LA REGLA CENTRAL DEL BLOQUE, y la que justifica todo lo demas.

        El saldo de una cuenta puente no dice nada por si solo: una que mueve
        veintiocho millones y los liquida cada semana esta sana. Lo que la
        define es si OSCILA o si ACUMULA. Se mira el saldo acumulado al cierre
        de cada mes de la ventana; si en todos mantuvo el mismo signo y en
        ninguno se acerco a cero, el circuito no esta cerrando y lo que hay
        ahi no es un saldo: es sedimento.

        Se calcula con una sola consulta por cuenta -- el saldo de apertura
        mas los movimientos agrupados por mes -- y no con una consulta por
        mes, que es como estas reglas se vuelven imposibles de correr.
        """
        hoy = fields.Date.context_today(self)
        Linea = self.env["account.move.line"]
        salida = []

        for p in self.env["ags.cuenta.puente"].search([
                ("activa", "=", True), ("company_id", "=", compania.id)]):
            if p.cierre_esperado != "cero":
                continue
            inicio = (hoy.replace(day=1)
                      - relativedelta(months=p.meses_ciclo - 1))
            base = [("account_id", "=", p.cuenta_id.id),
                    ("parent_state", "=", "posted"),
                    ("company_id", "=", compania.id)]

            # _read_group sin agrupacion devuelve una sola tupla con el
            # agregado, no una lista de pares.
            fila = Linea._read_group(
                base + [("date", "<", inicio)], [], ["balance:sum"])
            apertura = (fila[0][0] if fila else 0.0) or 0.0

            movimiento_por_mes = {}
            for mes, suma in Linea._read_group(
                    base + [("date", ">=", inicio)],
                    ["date:month"], ["balance:sum"]):
                if mes:
                    movimiento_por_mes[(mes.year, mes.month)] = suma or 0.0

            # Los meses se recorren completos y no solo los que tuvieron
            # movimiento: una cuenta puente que paso dos meses quieta con
            # saldo vivo es EXACTAMENTE el caso que esta regla busca, y
            # saltarsela por falta de apuntes seria mirar para otro lado.
            acumulado = apertura
            cierres = []
            cursor = inicio
            fin = hoy.replace(day=1)
            while cursor <= fin:
                acumulado += movimiento_por_mes.get(
                    (cursor.year, cursor.month), 0.0)
                cierres.append(acumulado)
                cursor += relativedelta(months=1)

            if len(cierres) < p.meses_ciclo:
                continue

            moneda = compania.currency_id
            cruzo = any(moneda.is_zero(s) for s in cierres) or any(
                (cierres[i] > 0) != (cierres[i + 1] > 0)
                for i in range(len(cierres) - 1))
            if cruzo:
                continue

            nombre = self._cuentas_en_idioma(p.cuenta_id).name or p.cuenta_id.code
            salida.append({
                "clave": "%s:%s" % (regla.codigo, p.id),
                "sujeto": "%s %s no cruzo cero en %s meses; saldo actual %s"
                          % (p.cuenta_id.code, nombre, p.meses_ciclo,
                             "{:,.2f}".format(cierres[-1])),
                "cantidad": 1,
                "modelo": "account.move.line",
                "dominio": base + [("full_reconcile_id", "=", False)],
            })
        return salida
