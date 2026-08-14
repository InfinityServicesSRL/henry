# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AgsCalculador(models.AbstractModel):
    """Motor de calculo de parametros desde los datos de Odoo.

    Cada parametro declara en metodo_tecnico el nombre del metodo que lo
    calcula. Ninguno escribe directo en la base: todos pasan por _registrar()
    para que la trazabilidad sea uniforme.

    CONVENCION DE SIGNOS EN account.move.line:
      balance = debit - credit
      Cuentas de ingreso  -> balance NEGATIVO (se acredita)
      Cuentas de gasto    -> balance POSITIVO (se debita)
    Por eso las ventas se obtienen invirtiendo el signo del balance.
    """
    _name = "ags.calculador"
    _description = "AG Intelligence - Motor de Calculo"

    # ==================================================================
    # INFRAESTRUCTURA COMUN
    # ==================================================================

    @api.model
    def _registrar(self, parametro, valor, fecha_periodo, origen="auto", notas=False):
        """Crea o actualiza la medicion de un parametro para un periodo."""
        Medicion = self.env["ags.medicion"]
        existente = Medicion.search([
            ("parametro_id", "=", parametro.id),
            ("fecha_periodo", "=", fecha_periodo),
        ], limit=1)
        vals = {
            "valor": valor,
            "origen": origen,
            "fecha_calculo": fields.Datetime.now(),
        }
        if notas:
            vals["notas"] = notas
        if existente:
            existente.write(vals)
            return existente
        vals.update({
            "parametro_id": parametro.id,
            "fecha_periodo": fecha_periodo,
        })
        return Medicion.create(vals)

    @api.model
    def _rango_mes(self, fecha=None):
        """Primer y ultimo dia del mes de la fecha dada."""
        fecha = fecha or fields.Date.context_today(self)
        primero = fecha.replace(day=1)
        ultimo = primero + relativedelta(months=1, days=-1)
        return primero, ultimo

    @api.model
    def _config(self):
        return self.env["ags.config"].get_config()

    @api.model
    def _saldo_cuentas(self, cuentas, desde, hasta, invertir=False):
        """Suma el balance de las cuentas dadas en el periodo, solo asientos
        publicados. Con invertir=True devuelve el signo contable natural de
        las cuentas de ingreso."""
        if not cuentas:
            return 0.0
        grupos = self.env["account.move.line"]._read_group(
            [
                ("account_id", "in", cuentas.ids),
                ("date", ">=", desde),
                ("date", "<=", hasta),
                ("parent_state", "=", "posted"),
            ],
            aggregates=["balance:sum"],
        )
        total = grupos[0][0] if grupos else 0.0
        total = total or 0.0
        return -total if invertir else total

    @api.model
    def _valor_movimientos(self, movimientos):
        """Valor absoluto de las capas de valoracion de unos movimientos."""
        if not movimientos:
            return 0.0
        capas = movimientos.mapped("stock_valuation_layer_ids")
        return abs(sum(capas.mapped("value"))) if capas else 0.0

    # ==================================================================
    # FASE 2A - SALUD DEL ERP
    # ==================================================================

    @api.model
    def _calc_ots_abiertas_vencidas(self, parametro, fecha=None):
        hoy = fecha or fields.Date.context_today(self)
        _, ultimo = self._rango_mes(hoy)
        cantidad = self.env["mrp.production"].search_count([
            ("state", "not in", ["done", "cancel"]),
            ("date_finished", "<", hoy),
        ])
        return self._registrar(parametro, cantidad, ultimo)

    @api.model
    def _calc_asientos_borrador(self, parametro, fecha=None):
        hoy = fecha or fields.Date.context_today(self)
        _, ultimo = self._rango_mes(hoy)
        cantidad = self.env["account.move"].search_count([
            ("state", "=", "draft"),
            ("move_type", "!=", "entry"),
        ])
        return self._registrar(parametro, cantidad, ultimo)

    @api.model
    def _calc_oc_sin_confirmar(self, parametro, fecha=None):
        hoy = fecha or fields.Date.context_today(self)
        _, ultimo = self._rango_mes(hoy)
        limite = hoy - relativedelta(days=15)
        cantidad = self.env["purchase.order"].search_count([
            ("state", "in", ["draft", "sent"]),
            ("date_order", "<=", limite),
        ])
        return self._registrar(parametro, cantidad, ultimo)

    @api.model
    def _calc_movimientos_sin_validar(self, parametro, fecha=None):
        hoy = fecha or fields.Date.context_today(self)
        _, ultimo = self._rango_mes(hoy)
        cantidad = self.env["stock.picking"].search_count([
            ("state", "not in", ["done", "cancel"]),
            ("scheduled_date", "<", hoy),
        ])
        return self._registrar(parametro, cantidad, ultimo)

    # ==================================================================
    # FASE 2B - MARGENES AGREGADOS
    # ==================================================================

    @api.model
    def _ventas_netas(self, desde, hasta):
        """Ventas netas del periodo, ya descontadas las notas de credito.

        Las notas de credito debitan la cuenta de ingreso, por lo que al sumar
        el balance de las cuentas de ingreso quedan restadas automaticamente.
        No hay que restarlas aparte: seria contarlas dos veces.
        """
        cfg = self._config()
        return self._saldo_cuentas(cfg.cuentas_ingreso(), desde, hasta, invertir=True)

    @api.model
    def _costo_ventas(self, desde, hasta):
        cfg = self._config()
        return self._saldo_cuentas(cfg.cuentas_costo_venta(), desde, hasta)

    @api.model
    def _calc_margen_bruto(self, parametro, fecha=None):
        """(Ventas netas - Costo de ventas) / Ventas netas * 100"""
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        costo = self._costo_ventas(desde, hasta)
        if not ventas:
            _logger.info("MARGEN_BRUTO: sin ventas entre %s y %s", desde, hasta)
            return False
        margen = ((ventas - costo) / ventas) * 100.0
        nota = "Ventas netas: %s | Costo de ventas: %s" % (
            round(ventas, 2), round(costo, 2))
        return self._registrar(parametro, margen, hasta, notas=nota)

    @api.model
    def _calc_margen_ebitda(self, parametro, fecha=None):
        """(Ventas - Costo de ventas - Gasto operativo) / Ventas * 100

        El gasto operativo excluye depreciacion, amortizacion, intereses e
        ISR, que es justamente lo que distingue al EBITDA del resultado neto.
        Esa exclusion depende de la configuracion de cuentas.
        """
        cfg = self._config()
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        costo = self._costo_ventas(desde, hasta)
        gasto = self._saldo_cuentas(cfg.cuentas_gasto_operativo(), desde, hasta)
        if not ventas:
            return False
        ebitda = ((ventas - costo - gasto) / ventas) * 100.0
        nota = "Ventas: %s | Costo: %s | Gasto operativo: %s" % (
            round(ventas, 2), round(costo, 2), round(gasto, 2))
        return self._registrar(parametro, ebitda, hasta, notas=nota)

    @api.model
    def _consumo_mp(self, desde, hasta):
        """Movimientos de consumo de MP y empaque en produccion.

        FILTRO CRITICO: se excluyen las categorias de reproceso. En AG Supply
        los combos consumen producto YA TERMINADO que salio de otras ordenes,
        de modo que el costo de la bobina se cuenta una vez al hacer el jumbo
        y otra al armar el combo.

        Medicion de julio 2026 sin el filtro: RD$ 14,422,598 de consumo contra
        ventas de 15,312,143 -- un 94% imposible. Con el filtro: 7,661,559,
        equivalente al 50% de las ventas, que es la cifra real.
        """
        cfg = self._config()
        categorias = cfg.categoria_mp_ids | cfg.categoria_empaque_ids
        if not categorias:
            return self.env["stock.move"], 0.0
        dominio = [
            ("state", "=", "done"),
            ("date", ">=", desde),
            ("date", "<=", hasta),
            ("raw_material_production_id", "!=", False),
            ("product_id.categ_id", "child_of", categorias.ids),
        ]
        if cfg.categoria_reproceso_ids:
            dominio.append(
                ("raw_material_production_id.product_id.categ_id",
                 "not child_of", cfg.categoria_reproceso_ids.ids))
        movimientos = self.env["stock.move"].search(dominio)
        return movimientos, self._valor_movimientos(movimientos)

    @api.model
    def _calc_mp_pct_ventas(self, parametro, fecha=None):
        """Costo de materia prima y empaque consumidos sobre ventas netas.

        Se mide el CONSUMO real en produccion, no las compras: comprar no es
        consumir, y confundirlos hace que el indicador salte con cada
        importacion de bobina.
        """
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        movimientos, costo_mp = self._consumo_mp(desde, hasta)
        if not costo_mp:
            _logger.warning("MP_PCT_VENTAS: sin consumo de MP en el periodo")
            return False
        pct = (costo_mp / ventas) * 100.0
        nota = "MP consumida: %s | Ventas: %s | Movimientos: %s" % (
            round(costo_mp, 2), round(ventas, 2), len(movimientos))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_merma_conversion(self, parametro, fecha=None):
        """Merma sobre materia prima consumida, en valor.

        ADVERTENCIA DE INTERPRETACION: el metodo por scrap solo captura lo que
        se registra explicitamente como desecho. Si en planta la merma no se
        registra de forma sistematica, este indicador subestimara la realidad.
        Conviene contrastarlo contra el consumo teorico de la LdM antes de
        congelar el baseline.
        """
        cfg = self._config()
        desde, hasta = self._rango_mes(fecha)
        categorias = cfg.categoria_mp_ids | cfg.categoria_empaque_ids
        if not categorias:
            return False

        _movs, valor_consumo = self._consumo_mp(desde, hasta)
        if not valor_consumo:
            return False

        desechos = self.env["stock.scrap"].search([
            ("state", "=", "done"),
            ("date_done", ">=", desde),
            ("date_done", "<=", hasta),
            ("product_id.categ_id", "child_of", categorias.ids),
        ])
        valor_merma = self._valor_movimientos(desechos.mapped("move_ids"))

        pct = (valor_merma / valor_consumo) * 100.0
        nota = "Merma: %s | Consumo MP: %s | Registros de desecho: %s" % (
            round(valor_merma, 2), round(valor_consumo, 2), len(desechos))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_margen_economico(self, parametro, fecha=None):
        """Margen bruto ajustado por el costo de financiar la cartera.

        Una venta al 22% cobrada a 90 dias no vale lo mismo que una al 22%
        cobrada a 30. La diferencia es lo que cuesta financiar esa cuenta por
        cobrar, y ese costo no aparece en ningun estado de resultados.

            costo_financiero_% = (DSO / dias_año) * tasa_anual
            margen_economico   = margen_bruto - costo_financiero_%

        La brecha entre este indicador y MARGEN_BRUTO es en si misma una
        medida: cuanto margen se va en financiar a los clientes.
        """
        cfg = self._config()
        _desde, hasta = self._rango_mes(fecha)
        Param = self.env["ags.parametro"]
        Medicion = self.env["ags.medicion"]

        p_margen = Param.search([("codigo", "=", "MARGEN_BRUTO")], limit=1)
        p_dso = Param.search([("codigo", "=", "DSO")], limit=1)
        if not p_margen or not p_dso:
            return False

        m_margen = Medicion.search([
            ("parametro_id", "=", p_margen.id),
            ("fecha_periodo", "=", hasta),
        ], limit=1)
        m_dso = Medicion.search([
            ("parametro_id", "=", p_dso.id),
            ("fecha_periodo", "=", hasta),
        ], limit=1)
        if not m_margen or not m_dso:
            _logger.info(
                "MARGEN_ECONOMICO: faltan MARGEN_BRUTO o DSO del periodo %s", hasta)
            return False

        costo_fin = (m_dso.valor / (cfg.dias_base_anio or 365)) * cfg.tasa_costo_capital
        economico = m_margen.valor - costo_fin
        nota = ("Margen bruto: %s%% | DSO: %s dias | Tasa: %s%% | "
                "Costo financiero: %s%%") % (
            round(m_margen.valor, 2), round(m_dso.valor, 1),
            cfg.tasa_costo_capital, round(costo_fin, 2))
        return self._registrar(parametro, economico, hasta, notas=nota)

    # ==================================================================
    # FASE 2B - ENERGIA
    # ==================================================================

    @api.model
    def _kwh_del_mes(self, desde, hasta):
        """kWh facturados, imputados al mes de CONSUMO.

        La distribuidora factura al mes siguiente. Sin este ajuste, el ratio
        compararia energia de un mes contra produccion de otro: un desfase
        sistematico que no se corrige promediando periodos.
        """
        cfg = self._config()
        if not cfg.proveedor_energia_id:
            return 0.0, 0.0
        if cfg.energia_mes_anterior:
            f_desde = desde + relativedelta(months=1)
            f_hasta = f_desde + relativedelta(months=1, days=-1)
        else:
            f_desde, f_hasta = desde, hasta
        facturas = self.env["account.move"].search([
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("partner_id", "=", cfg.proveedor_energia_id.id),
            ("invoice_date", ">=", f_desde),
            ("invoice_date", "<=", f_hasta),
        ])
        kwh = sum(facturas.mapped("ags_kwh_consumidos"))
        monto = sum(facturas.mapped("amount_untaxed"))
        return kwh, monto

    @api.model
    def _toneladas_convertidas(self, desde, hasta):
        """Toneladas de producto terminado producidas en el periodo.

        Depende de que los productos tengan peso configurado. Si el peso esta
        en cero, la tonelada no se puede derivar y el indicador queda sin dato.
        """
        cfg = self._config()
        dominio = [
            ("state", "=", "done"),
            ("date_finished", ">=", desde),
            ("date_finished", "<=", hasta),
        ]
        if cfg.categoria_pt_ids:
            dominio.append(("product_id.categ_id", "child_of", cfg.categoria_pt_ids.ids))
        ordenes = self.env["mrp.production"].search(dominio)
        kg = sum((o.qty_produced or 0.0) * (o.product_id.weight or 0.0) for o in ordenes)
        return kg / 1000.0

    @api.model
    def _calc_kwh_por_tonelada(self, parametro, fecha=None):
        desde, hasta = self._rango_mes(fecha)
        kwh, _monto = self._kwh_del_mes(desde, hasta)
        toneladas = self._toneladas_convertidas(desde, hasta)
        if not toneladas or not kwh:
            return False
        valor = kwh / toneladas
        nota = "kWh: %s | Toneladas: %s" % (round(kwh, 2), round(toneladas, 3))
        return self._registrar(parametro, valor, hasta, notas=nota)

    @api.model
    def _calc_costo_kwh(self, parametro, fecha=None):
        desde, hasta = self._rango_mes(fecha)
        kwh, monto = self._kwh_del_mes(desde, hasta)
        if not kwh:
            return False
        valor = monto / kwh
        nota = "Monto: %s | kWh: %s" % (round(monto, 2), round(kwh, 2))
        return self._registrar(parametro, valor, hasta, notas=nota)


    # ==================================================================
    # FASE 2C - CARTERA, PLAZOS Y DISCIPLINA COMERCIAL
    # ==================================================================

    @api.model
    def _fecha_cobro(self, factura):
        """Fecha del ultimo cobro conciliado contra la factura."""
        lineas = factura.line_ids.filtered(
            lambda l: l.account_id.account_type == "asset_receivable")
        fechas = []
        for l in lineas:
            for pr in l.matched_credit_ids:
                if pr.credit_move_id.date:
                    fechas.append(pr.credit_move_id.date)
        return max(fechas) if fechas else None

    @api.model
    def _calc_dso(self, parametro, fecha=None):
        """DSO estandar: (CxC al cierre / ventas del periodo) * dias del periodo.

        CORRECCION IMPORTANTE: el saldo se reconstruye A LA FECHA DE CIERRE del
        periodo, no al momento de ejecutar el calculo. Usar el residual actual
        haria que el DSO de julio se midiera con la cobranza de agosto ya
        aplicada, y la cartera pareceria mucho mas sana de lo que estuvo.

        Para reconstruirlo se toma el saldo de las lineas por cobrar emitidas
        hasta la fecha de cierre, menos los cobros conciliados que ocurrieron
        hasta esa misma fecha.
        """
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False

        lineas = self.env["account.move.line"].search([
            ("account_id.account_type", "=", "asset_receivable"),
            ("date", "<=", hasta),
            ("parent_state", "=", "posted"),
        ])
        saldo = 0.0
        for l in lineas:
            saldo += l.debit - l.credit
            # Restar solo lo conciliado HASTA la fecha de cierre
            for pr in l.matched_credit_ids:
                if pr.credit_move_id.date and pr.credit_move_id.date <= hasta:
                    saldo -= pr.amount
            for pr in l.matched_debit_ids:
                if pr.debit_move_id.date and pr.debit_move_id.date <= hasta:
                    saldo += pr.amount

        dias = (hasta - desde).days + 1
        valor = (saldo / ventas) * dias
        nota = "CxC al %s: %s | Ventas: %s | Dias: %s" % (
            hasta, round(saldo, 2), round(ventas, 2), dias)
        return self._registrar(parametro, valor, hasta, notas=nota)

    @api.model
    def _calc_desviacion_plazo(self, parametro, fecha=None):
        """Dias reales de cobro menos pactados, sobre COHORTE MADURA.

        POR QUE ESPERA 90 DIAS: si se evalua el mes recien cerrado, solo
        entran las facturas ya cobradas -- que son justamente las de los
        clientes rapidos. Medicion de julio 2026 con ese sesgo: 89 facturas
        cobradas contra 219 aun pendientes. El indicador daba -18.5 dias,
        sugiriendo que se cobra antes de lo pactado, cuando en realidad se
        estaba midiendo solo a los buenos pagadores.

        La cohorte de un mes se considera madura cuando han pasado 90 dias
        desde su cierre. Antes de eso el parametro no registra medicion: es
        preferible no mostrar numero a mostrar uno sesgado.

        Este indicador mira hacia atras y evalua disciplina de cobro. Para
        gestion del dia a dia esta ATRASO_CARTERA_VIVA.
        """
        desde, hasta = self._rango_mes(fecha)
        hoy = fields.Date.context_today(self)
        dias_madurez = 90
        if (hoy - hasta).days < dias_madurez:
            faltan = dias_madurez - (hoy - hasta).days
            _logger.info(
                "DESVIACION_PLAZO: cohorte de %s inmadura, faltan %s dias",
                hasta, faltan)
            return False

        facturas = self.env["account.move"].search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
        ])
        peso = suma = 0.0
        n = sin_cobrar = 0
        for f in facturas:
            if not f.invoice_date_due:
                continue
            cobro = self._fecha_cobro(f)
            if not cobro:
                sin_cobrar += 1
                continue
            pactado = (f.invoice_date_due - f.invoice_date).days
            real = (cobro - f.invoice_date).days
            monto = f.amount_untaxed or 0.0
            suma += (real - pactado) * monto
            peso += monto
            n += 1
        if not peso:
            return False
        valor = suma / peso
        nota = ("Cohorte madura | Evaluadas: %s | Nunca cobradas: %s | "
                "Ponderado por monto") % (n, sin_cobrar)
        return self._registrar(parametro, valor, hasta, notas=nota)

    @api.model
    def _calc_atraso_cartera_viva(self, parametro, fecha=None):
        """Dias promedio de vencimiento de lo que HOY esta pendiente.

        A diferencia de DESVIACION_PLAZO, este mira el presente y es
        accionable: de la cartera abierta en este momento, cuantos dias
        promedio lleva vencida, ponderado por monto.

        Es el indicador de gestion diaria de cobranza. Un valor negativo
        significa que la cartera esta mayormente por vencer; uno positivo,
        que en promedio ya vencio.
        """
        _desde, hasta = self._rango_mes(fecha)
        lineas = self.env["account.move.line"].search([
            ("account_id.account_type", "=", "asset_receivable"),
            ("parent_state", "=", "posted"),
            ("full_reconcile_id", "=", False),
            ("date_maturity", "!=", False),
            ("date", "<=", hasta),
        ])
        peso = suma = 0.0
        vencido = por_vencer = 0.0
        for l in lineas:
            saldo = l.amount_residual
            if not saldo:
                continue
            dias = (hasta - l.date_maturity).days
            suma += dias * saldo
            peso += saldo
            if dias > 0:
                vencido += saldo
            else:
                por_vencer += saldo
        if not peso:
            return False
        valor = suma / peso
        nota = "Cartera viva: %s | Vencido: %s | Por vencer: %s" % (
            round(peso, 2), round(vencido, 2), round(por_vencer, 2))
        return self._registrar(parametro, valor, hasta, notas=nota)

    @api.model
    def _calc_pct_cartera_corriente(self, parametro, fecha=None):
        """Proporcion de la cartera abierta que aun no ha vencido.

        Es mas robusto que el DSO para leer salud de cartera, porque no se
        distorsiona con dos o tres saldos grandes.
        """
        _desde, hasta = self._rango_mes(fecha)
        lineas = self.env["account.move.line"].search([
            ("account_id.account_type", "=", "asset_receivable"),
            ("parent_state", "=", "posted"),
            ("full_reconcile_id", "=", False),
            ("date", "<=", hasta),
        ])
        total = corriente = 0.0
        tramos = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
        for l in lineas:
            saldo = l.amount_residual
            if not saldo:
                continue
            total += saldo
            venc = l.date_maturity or l.date
            dias = (hasta - venc).days
            if dias <= 0:
                corriente += saldo
                tramos["0-30"] += saldo
            elif dias <= 30:
                tramos["0-30"] += saldo
            elif dias <= 60:
                tramos["31-60"] += saldo
            elif dias <= 90:
                tramos["61-90"] += saldo
            else:
                tramos["90+"] += saldo
        if not total:
            return False
        pct = (corriente / total) * 100.0
        nota = "Total: %s | 0-30: %s | 31-60: %s | 61-90: %s | 90+: %s" % (
            round(total, 0), round(tramos["0-30"], 0), round(tramos["31-60"], 0),
            round(tramos["61-90"], 0), round(tramos["90+"], 0))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _nc_por_motivo(self, desde, hasta, motivos):
        """Suma de notas de credito de los motivos indicados."""
        ncs = self.env["account.move"].search([
            ("move_type", "=", "out_refund"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
            ("ags_motivo_nc", "in", motivos),
        ])
        return sum(ncs.mapped("amount_untaxed")), len(ncs)

    @api.model
    def _calc_pct_pronto_pago(self, parametro, fecha=None):
        """Costo del programa de pronto pago sobre ventas.

        Es un costo financiero deliberado, no una perdida operativa. Se separa
        de las devoluciones justamente para poder juzgarlo como lo que es:
        el precio de cobrar antes.
        """
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        monto, n = self._nc_por_motivo(desde, hasta, ["pronto_pago"])
        pct = (monto / ventas) * 100.0
        nota = "Descuentos: %s en %s notas | Ventas: %s" % (
            round(monto, 2), n, round(ventas, 2))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_pct_devoluciones(self, parametro, fecha=None):
        """Devoluciones reales sobre ventas.

        EXCLUYE las anulaciones por refacturacion: en factura electronica una
        factura emitida no se modifica, se anula con NC completa y se rehace.
        Contar esas anulaciones como devolucion inflaria el indicador con
        correcciones administrativas que no reflejan falla de producto.
        """
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        motivos = self.env["account.move"].MOTIVOS_DEVOLUCION
        monto, n = self._nc_por_motivo(desde, hasta, motivos)
        pct = (monto / ventas) * 100.0
        nota = "Devoluciones: %s en %s notas | Ventas: %s" % (
            round(monto, 2), n, round(ventas, 2))
        return self._registrar(parametro, pct, hasta, notas=nota)



    @api.model
    def _calc_pct_garantia_comercial(self, parametro, fecha=None):
        """Costo de la politica de recompra sobre ventas.

        AG Supply recibe de vuelta la mercancia que el cliente no logro
        vender, haya pagado o no. Eso es una garantia comercial deliberada:
        un argumento de venta que baja la barrera de entrada al distribuidor.

        Se mide aparte de las devoluciones por falla porque son cosas
        distintas: esta es el precio de una politica que se eligio, aquella
        es un error que se pudo evitar. Si el numero sube, la pregunta no es
        "quien se equivoco" sino "que producto no esta rotando en el punto
        de venta", que es informacion comercial valiosa.
        """
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        motivos = self.env["account.move"].MOTIVOS_COMERCIALES
        monto, n = self._nc_por_motivo(desde, hasta, motivos)
        pct = (monto / ventas) * 100.0
        nota = "Garantia y descuentos: %s en %s notas | Ventas: %s" % (
            round(monto, 2), n, round(ventas, 2))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_pct_errores_facturacion(self, parametro, fecha=None):
        """Notas de credito por correccion administrativa sobre ventas.

        Agrupa error de precio, refacturacion por e-CF, consumo interno mal
        facturado y datos fiscales incorrectos. No son devoluciones ni cuestan
        producto, pero cada una consume tiempo de facturacion, de contabilidad
        y de relacion con el cliente.

        En el semestre feb-jul 2026 el error de precio solo aparecio en 25 de
        155 notas: uno de cada seis documentos salia mal facturado de precio.
        """
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        motivos = self.env["account.move"].MOTIVOS_ADMINISTRATIVOS
        monto, n = self._nc_por_motivo(desde, hasta, motivos)
        pct = (monto / ventas) * 100.0
        nota = "Correcciones: %s en %s notas | Ventas: %s" % (
            round(monto, 2), n, round(ventas, 2))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_nc_sin_clasificar(self, parametro, fecha=None):
        """% de notas de credito sin motivo asignado.

        Es un indicador de calidad del propio sistema de medicion: mientras
        esta cifra sea alta, los indicadores de devoluciones y de errores
        estan incompletos y no deben leerse como definitivos.
        """
        desde, hasta = self._rango_mes(fecha)
        todas = self.env["account.move"].search([
            ("move_type", "=", "out_refund"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
        ])
        if not todas:
            return False
        sin = todas.filtered(lambda n: not n.ags_motivo_nc)
        pct = (len(sin) / len(todas)) * 100.0
        nota = "Sin motivo: %s de %s notas | Monto: %s" % (
            len(sin), len(todas), round(sum(sin.mapped("amount_untaxed")), 2))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_cumplimiento_pronto_pago(self, parametro, fecha=None):
        """% de descuentos otorgados donde el pago SI llego dentro del plazo.

        El descuento por pronto pago solo tiene sentido si compra dias. Si se
        otorga sobre facturas cobradas fuera de plazo, se esta pagando por un
        adelanto que no ocurrio.
        """
        cfg = self._config()
        desde, hasta = self._rango_mes(fecha)
        ncs = self.env["account.move"].search([
            ("move_type", "=", "out_refund"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
            ("ags_motivo_nc", "=", "pronto_pago"),
        ])
        if not ncs:
            return False
        limite = cfg.dias_pronto_pago + cfg.dias_gracia_pronto_pago
        cumplen = fuera = 0
        m_cumplen = m_fuera = 0.0
        for nc in ncs:
            orig = nc.reversed_entry_id
            if not orig:
                continue
            cobro = self._fecha_cobro(orig)
            if not cobro or not orig.invoice_date:
                continue
            dias = (cobro - orig.invoice_date).days
            if dias <= limite:
                cumplen += 1
                m_cumplen += nc.amount_untaxed
            else:
                fuera += 1
                m_fuera += nc.amount_untaxed
        total = cumplen + fuera
        if not total:
            return False
        pct = (cumplen / total) * 100.0
        nota = ("Dentro de plazo: %s (%s) | Fuera: %s (%s) | Limite: %s dias") % (
            cumplen, round(m_cumplen, 2), fuera, round(m_fuera, 2), limite)
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_ventas_sin_termino(self, parametro, fecha=None):
        """% de ventas facturadas sin termino de pago pactado.

        Sin termino, la factura nace vencida el mismo dia. Para una venta de
        contado eso es correcto; para un cliente a credito significa que se
        esta facturando sin marco de credito definido.
        """
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        facturas = self.env["account.move"].search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
            ("invoice_payment_term_id", "=", False),
        ])
        monto = sum(facturas.mapped("amount_untaxed"))
        pct = (monto / ventas) * 100.0
        nota = "Sin termino: %s facturas por %s" % (len(facturas), round(monto, 2))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_costo_conversion(self, parametro, fecha=None):
        """Mano de obra directa sobre ventas netas.

        Es la diferencia entre el margen sobre materiales y el margen bruto
        contable: cuanto cuesta transformar bobina en producto terminado.
        """
        cfg = self._config()
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        mod = self._saldo_cuentas(cfg.cuentas_mod(), desde, hasta)
        pct = (mod / ventas) * 100.0
        nota = "MOD: %s | Ventas: %s | Cuentas: %s" % (
            round(mod, 2), round(ventas, 2), len(cfg.cuentas_mod()))
        return self._registrar(parametro, pct, hasta, notas=nota)


    # ==================================================================
    # FASE 2D - CALIDAD DEL REGISTRO
    # ==================================================================

    @api.model
    def _calc_ingresos_sin_factura(self, parametro, fecha=None):
        """Ingresos registrados por asiento manual, sin documento de venta.

        Detectado en julio 2026: RD$ 48,138 de diferencia entre el saldo de
        las cuentas de ingreso y lo facturado. Dinero entrando a cuentas de
        venta sin factura detras.

        No siempre es error, pero siempre merece explicacion.
        """
        desde, hasta = self._rango_mes(fecha)
        cfg = self._config()
        ventas = self._saldo_cuentas(cfg.cuentas_ingreso(), desde, hasta, invertir=True)
        if not ventas:
            return False
        lineas = self.env["account.move.line"].search([
            ("account_id", "in", cfg.cuentas_ingreso().ids),
            ("date", ">=", desde), ("date", "<=", hasta),
            ("parent_state", "=", "posted"),
            ("move_id.move_type", "=", "entry"),
        ])
        monto = abs(sum(lineas.mapped("balance")))
        pct = (monto / ventas) * 100.0
        nota = "Sin factura: %s en %s lineas | Ventas: %s" % (
            round(monto, 2), len(lineas), round(ventas, 2))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_ajustes_inventario(self, parametro, fecha=None):
        """Cantidad de ajustes de inventario del mes.

        Una frecuencia alta y sostenida indica que el stock del sistema no se
        esta confiando. Mientras eso ocurra, ninguna medicion de merma ni de
        costo de materiales es confiable, porque la base de valoracion se
        esta corrigiendo a mano.
        """
        desde, hasta = self._rango_mes(fecha)
        cantidad = self.env["stock.move"].search_count([
            ("is_inventory", "=", True), ("state", "=", "done"),
            ("date", ">=", desde), ("date", "<=", hasta),
        ])
        return self._registrar(parametro, cantidad, hasta)

    @api.model
    def _calc_ajustes_reversados(self, parametro, fecha=None):
        """Ajustes que se anulan entre si: mismo producto, misma cantidad,
        signos opuestos, dentro de una ventana corta.

        Ese patron no es ajuste de inventario sino correccion de un error de
        registro. Se detecto revisando a mano los ajustes de bobina: aparecen
        pares exactos con dias de diferencia.

        Es un indicador de calidad del proceso de registro, no del inventario.
        """
        desde, hasta = self._rango_mes(fecha)
        movs = self.env["stock.move"].search([
            ("is_inventory", "=", True), ("state", "=", "done"),
            ("date", ">=", desde), ("date", "<=", hasta),
        ])
        if not movs:
            return False
        por_prod = {}
        for m in movs:
            signo = 1 if m.location_id.usage == "inventory" else -1
            q = round(m.product_uom_qty * signo, 2)
            por_prod.setdefault(m.product_id.id, []).append((q, m.date))
        pares = 0
        for _pid, lista in por_prod.items():
            usados = set()
            for i, (q1, d1) in enumerate(lista):
                if i in usados or not q1:
                    continue
                for j, (q2, d2) in enumerate(lista):
                    if j <= i or j in usados:
                        continue
                    if abs(q1 + q2) < 0.01 and abs((d2 - d1).days) <= 7:
                        pares += 1
                        usados.add(i); usados.add(j)
                        break
        pct = (pares * 2 / len(movs)) * 100.0
        nota = "Pares que se anulan: %s | Total ajustes: %s" % (pares, len(movs))
        return self._registrar(parametro, pct, hasta, notas=nota)


    @api.model
    def _calc_compras_sin_termino(self, parametro, fecha=None):
        """% de facturas de compra sin termino de pago pactado.

        Sin termino la factura nace vencida el mismo dia, y el aging la
        clasifica como tal desde el primer momento. Parte del "vencido" de
        cuentas por pagar es artefacto de esto, no atraso real.

        Medicion feb-jul 2026: 880 de 1,417 facturas de compra sin termino
        (62%), por RD$ 25.8 millones.
        """
        desde, hasta = self._rango_mes(fecha)
        facturas = self.env["account.move"].search([
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
        ])
        if not facturas:
            return False
        sin = facturas.filtered(lambda f: not f.invoice_payment_term_id)
        pct = (len(sin) / len(facturas)) * 100.0
        nota = "Sin termino: %s de %s facturas | Monto: %s" % (
            len(sin), len(facturas), round(sum(sin.mapped("amount_untaxed")), 2))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_cxp_comercial_vencida(self, parametro, fecha=None):
        """% vencido de la cartera de proveedores COMERCIALES.

        Excluye acreedores financieros: prestamos, cooperativas y bancos
        figuran siempre al 100% vencido porque su amortizacion no responde a
        terminos de pago de factura. Incluirlos producia un 78% que sugeria
        crisis de pagos cuando los proveedores de bobina estaban al dia.
        """
        _desde, hasta = self._rango_mes(fecha)
        registros = self.env["ags.aging"].search([
            ("tipo", "=", "cxp"), ("fecha_corte", "=", hasta),
            ("tipo_acreedor", "in", ["comercial", False]),
        ])
        if not registros:
            return False
        total = sum(registros.mapped("saldo_total"))
        venc = sum(registros.mapped("vencido"))
        if not total:
            return False
        pct = (venc / total) * 100.0
        nota = "CxP comercial: %s | Vencido: %s | Proveedores: %s" % (
            round(total, 0), round(venc, 0), len(registros))
        return self._registrar(parametro, pct, hasta, notas=nota)

    # ==================================================================
    # ORQUESTACION
    # ==================================================================

    @api.model
    def calcular_periodo(self, fecha=None, codigos=None):
        """Ejecuta los calculadores disponibles para un periodo.

        El orden importa: MARGEN_ECONOMICO depende de que MARGEN_BRUTO y DSO
        ya esten calculados para el mismo periodo. Por eso se ordena por
        secuencia y el margen economico lleva secuencia alta.

        Devuelve un resumen para poder auditar la corrida.
        """
        dominio = [("captura", "=", "auto"), ("metodo_tecnico", "!=", False)]
        if codigos:
            dominio.append(("codigo", "in", codigos))
        parametros = self.env["ags.parametro"].search(dominio, order="secuencia, id")

        resultados = {"ok": [], "sin_datos": [], "error": []}
        for param in parametros:
            metodo = getattr(self, param.metodo_tecnico, None)
            if not metodo:
                resultados["error"].append((param.codigo, "metodo no implementado"))
                continue
            try:
                res = metodo(param, fecha)
                if res:
                    resultados["ok"].append(param.codigo)
                else:
                    resultados["sin_datos"].append(param.codigo)
            except Exception as e:
                _logger.exception("Error calculando %s", param.codigo)
                resultados["error"].append((param.codigo, str(e)))
        return resultados
