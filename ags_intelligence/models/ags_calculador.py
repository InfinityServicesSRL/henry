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
    def _registrar(self, parametro, valor, fecha_periodo, origen="auto",
                   notas=False, evidencia=None):
        """Crea o actualiza la medicion de un parametro para un periodo.

        evidencia es el CONTEO DE REGISTROS BASE que sustentan el numero, no
        el numero. Un calculador que divide entre cero registros produce un
        cero perfectamente valido en aritmetica y completamente falso en
        significado: MERMA_CONVERSION daba 0.00% en verde con cero registros
        de scrap, felicitando a una planta por no registrar su merma.

        Dejarlo en None mantiene el comportamiento anterior intacto, para que
        los 54 calculadores lo adopten de a uno y no de golpe.
        """
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
        if evidencia is not None:
            # Se escriben ambos en cada corrida: si la evidencia vuelve, el
            # indicador tiene que salir solo del estado sin_evidencia.
            vals["evidencia_n"] = evidencia
            vals["sin_evidencia"] = not evidencia
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
            ("date", "<=", "%s 23:59:59" % hasta),
            ("raw_material_production_id", "!=", False),
            ("product_id.categ_id", "child_of", categorias.ids),
        ]
        if cfg.categoria_reproceso_ids:
            # "not child_of" NO es un operador de Odoo: el parser de dominios
            # lo rechaza con "Invalid leaf". La jerarquia se resuelve primero
            # y la exclusion se expresa como un "not in" sobre productos, que
            # ademas deja explicito que se excluyen ORDENES cuyo producto
            # terminado es de reproceso, no los movimientos de MP.
            reproceso = self.env["product.product"].search([
                ("categ_id", "child_of", cfg.categoria_reproceso_ids.ids),
            ])
            if reproceso:
                dominio.append(
                    ("raw_material_production_id.product_id", "not in",
                     reproceso.ids))
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
            # Antes se devolvia False y el indicador desaparecia en silencio.
            # Un mes CON ventas y sin un solo movimiento de consumo de MP no
            # es "sin dato": es una fabrica que no registro su consumo, o una
            # configuracion de categorias vacia. Las dos cosas hay que verlas.
            _logger.warning("MP_PCT_VENTAS: sin consumo de MP en el periodo")
            return self._registrar(
                parametro, 0.0, hasta, evidencia=0,
                notas="Ventas por %s sin un solo movimiento de consumo de "
                      "materia prima en el periodo." % round(ventas, 2))
        pct = (costo_mp / ventas) * 100.0
        nota = "MP consumida: %s | Ventas: %s | Movimientos: %s" % (
            round(costo_mp, 2), round(ventas, 2), len(movimientos))
        return self._registrar(parametro, pct, hasta, notas=nota,
                               evidencia=len(movimientos))

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
        if not desechos:
            nota += (" | Cero registros de desecho contra %s de MP consumida: "
                     "el 0%% no es merma cero, es merma no registrada."
                     % round(valor_consumo, 2))
        # La evidencia son los REGISTROS DE DESECHO, no el consumo. Sin scrap
        # el numerador es cero por construccion y el semaforo felicitaria a
        # una planta que no registra su merma. Es el caso que motivo D14.
        return self._registrar(parametro, pct, hasta, notas=nota,
                               evidencia=len(desechos))

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
            # El saldo por vencer va SOLO a corriente. Antes se sumaba
            # tambien al tramo 0-30, lo que hacia que los tramos superaran
            # el total y el porcentaje pasara de 100.
            if dias <= 0:
                corriente += saldo
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
        pct = min((corriente / total) * 100.0, 100.0)
        nota = ("Total: %s | Por vencer: %s | 1-30: %s | 31-60: %s | "
                "61-90: %s | 90+: %s") % (
            round(total, 0), round(corriente, 0), round(tramos["0-30"], 0),
            round(tramos["31-60"], 0), round(tramos["61-90"], 0),
            round(tramos["90+"], 0))
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
            ("date", ">=", desde), ("date", "<=", "%s 23:59:59" % hasta),
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
            ("date", ">=", desde), ("date", "<=", "%s 23:59:59" % hasta),
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
        # Los acuerdos de pago negociados y las partes relacionadas ya quedan
        # fuera por el dominio anterior: su calendario no responde a terminos
        # de factura y mezclarlos distorsiona la lectura de disciplina de pago.
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
    # EXPOSICION CAMBIARIA
    # ==================================================================

    @api.model
    def _saldos_por_moneda(self, tipo_cuenta, hasta):
        """Saldos abiertos agrupados por moneda, en su moneda original y en DOP.

        Una factura en USD registrada hace dos anios vale hoy algo distinto en
        pesos aunque nadie haya pagado ni comprado nada. Verla solo convertida
        esconde de donde viene el movimiento.
        """
        lineas = self.env["account.move.line"].search([
            ("account_id.account_type", "=", tipo_cuenta),
            ("parent_state", "=", "posted"),
            ("date", "<=", hasta),
            ("full_reconcile_id", "=", False),
        ])
        base = self.env.company.currency_id
        por_moneda = {}
        for l in lineas:
            saldo_dop = l.amount_residual
            if not saldo_dop:
                continue
            mon = l.currency_id or base
            x = por_moneda.setdefault(mon.id, {
                "moneda": mon.name, "es_base": mon == base,
                "saldo_dop": 0.0, "saldo_original": 0.0,
            })
            x["saldo_dop"] += saldo_dop
            x["saldo_original"] += (
                l.amount_residual_currency if l.currency_id and l.currency_id != base
                else saldo_dop
            )
        return por_moneda

    @api.model
    def _calc_exposicion_usd(self, parametro, fecha=None):
        """Exposicion cambiaria neta: pasivos en USD menos activos en USD.

        AG Supply compra bobina en dolares y vende en pesos. Cada movimiento
        del tipo de cambio le mueve el valor de la deuda sin que nadie haya
        comprado ni pagado nada.

        Un valor POSITIVO significa que se debe mas de lo que se tiene por
        cobrar en USD: si el peso se deprecia, la deuda crece en pesos.
        """
        _desde, hasta = self._rango_mes(fecha)
        base = self.env.company.currency_id
        usd = self.env["res.currency"].search([("name", "=", "USD")], limit=1)
        if not usd:
            return False

        cxp = self._saldos_por_moneda("liability_payable", hasta)
        cxc = self._saldos_por_moneda("asset_receivable", hasta)
        deuda = -cxp.get(usd.id, {}).get("saldo_original", 0.0)
        cobrar = cxc.get(usd.id, {}).get("saldo_original", 0.0)
        neta = deuda - cobrar

        tasa = usd._get_conversion_rate(usd, base, self.env.company, hasta)
        nota = ("CxP USD: %s | CxC USD: %s | Neta: %s USD | "
                "Equivale a %s DOP a tasa %s") % (
            round(deuda, 2), round(cobrar, 2), round(neta, 2),
            round(neta * tasa, 2), round(tasa, 2))
        return self._registrar(parametro, neta, hasta, notas=nota)

    @api.model
    def _calc_sensibilidad_cambiaria(self, parametro, fecha=None):
        """Cuanto cambia el resultado por cada peso de movimiento en el dolar.

        Mas util que la exposicion en bruto: traduce el riesgo a lo que
        realmente importa, que es el impacto en pesos sobre el resultado.

        Se expresa como el efecto de una depreciacion de RD$ 1.00 por dolar
        sobre la posicion neta. Positivo significa perdida ante depreciacion.
        """
        _desde, hasta = self._rango_mes(fecha)
        usd = self.env["res.currency"].search([("name", "=", "USD")], limit=1)
        if not usd:
            return False
        cxp = self._saldos_por_moneda("liability_payable", hasta)
        cxc = self._saldos_por_moneda("asset_receivable", hasta)
        deuda = -cxp.get(usd.id, {}).get("saldo_original", 0.0)
        cobrar = cxc.get(usd.id, {}).get("saldo_original", 0.0)
        neta = deuda - cobrar
        nota = ("Posicion neta: %s USD. Cada RD$1.00 de depreciacion "
                "impacta el resultado en %s DOP") % (round(neta, 2), round(neta, 2))
        return self._registrar(parametro, neta, hasta, notas=nota)

    @api.model
    def _calc_pct_cxp_usd(self, parametro, fecha=None):
        """Porcentaje de la CxP denominada en moneda extranjera."""
        _desde, hasta = self._rango_mes(fecha)
        base = self.env.company.currency_id
        cxp = self._saldos_por_moneda("liability_payable", hasta)
        total = sum(abs(v["saldo_dop"]) for v in cxp.values())
        if not total:
            return False
        extranjera = sum(
            abs(v["saldo_dop"]) for v in cxp.values() if not v["es_base"])
        pct = (extranjera / total) * 100.0
        detalle = " | ".join(
            "%s: %s" % (v["moneda"], round(abs(v["saldo_original"]), 0))
            for v in cxp.values() if not v["es_base"])
        nota = "CxP total: %s | En moneda extranjera: %s | %s" % (
            round(total, 0), round(extranjera, 0), detalle or "ninguna")
        return self._registrar(parametro, pct, hasta, notas=nota)


    # ==================================================================
    # ABASTECIMIENTO INTERNACIONAL
    # ==================================================================

    @api.model
    def _calc_costo_sustitucion(self, parametro, fecha=None):
        """Costo total de comprar local por retrasos de importacion.

        Suma sobrecosto de material, costo financiero por credito perdido y
        disrupcion estimada. Es el numero que permite negociar con proveedores
        y navieras usando cifras en vez de anecdotas.
        """
        desde, hasta = self._rango_mes(fecha)
        ocs = self.env["purchase.order"].search([
            ("ags_es_sustituta", "=", True),
            ("state", "in", ["purchase", "done"]),
            ("date_order", ">=", desde),
            ("date_order", "<=", hasta),
        ])
        if not ocs:
            return False
        total = sum(ocs.mapped("ags_costo_total_evento"))
        mat = sum(ocs.mapped("ags_sobrecosto_material"))
        fin = sum(ocs.mapped("ags_costo_financiero"))
        dis = sum(ocs.mapped("ags_costo_disrupcion"))
        nota = "Eventos: %s | Material: %s | Financiero: %s | Disrupcion: %s" % (
            len(ocs), round(mat, 2), round(fin, 2), round(dis, 2))
        return self._registrar(parametro, total, hasta, notas=nota)

    @api.model
    def _calc_lead_time_real(self, parametro, fecha=None):
        """Dias entre la orden y la recepcion efectiva, ponderado por monto.

        Alimenta el punto de reorden: si un proveedor tarda 120 dias en lugar
        de los 60 pactados, el stock de seguridad tiene que ser el doble.
        """
        desde, hasta = self._rango_mes(fecha)
        pickings = self.env["stock.picking"].search([
            ("picking_type_id.code", "=", "incoming"),
            ("state", "=", "done"),
            ("date_done", ">=", desde),
            ("date_done", "<=", hasta),
            ("purchase_id", "!=", False),
        ])
        peso = suma = 0.0
        n = 0
        for p in pickings:
            oc = p.purchase_id
            if not oc.date_order or not p.date_done:
                continue
            dias = (p.date_done.date() - oc.date_order.date()).days
            monto = oc.amount_untaxed or 0.0
            if monto <= 0 or dias < 0:
                continue
            suma += dias * monto
            peso += monto
            n += 1
        if not peso:
            return False
        valor = suma / peso
        nota = "Recepciones evaluadas: %s | Ponderado por monto" % n
        return self._registrar(parametro, valor, hasta, notas=nota)

    @api.model
    def _calc_pct_entrega_completa(self, parametro, fecha=None):
        """Porcentaje de lo pedido que efectivamente se recibe.

        Medicion de julio 2026: Vipa entrego 43,498 de 66,000 unidades (66%)
        y Bridge View 6,022 de 10,000 (60%). Planificar con cantidades que no
        se materializan produce quiebres.
        """
        desde, hasta = self._rango_mes(fecha)
        ocs = self.env["purchase.order"].search([
            ("state", "in", ["purchase", "done"]),
            ("date_order", ">=", desde),
            ("date_order", "<=", hasta),
        ])
        ped = rec = 0.0
        for o in ocs:
            for l in o.order_line:
                ped += l.product_qty or 0.0
                rec += min(l.qty_received or 0.0, l.product_qty or 0.0)
        if not ped:
            return False
        pct = (rec / ped) * 100.0
        nota = "Pedido: %s | Recibido: %s | Ordenes: %s" % (
            round(ped, 0), round(rec, 0), len(ocs))
        return self._registrar(parametro, pct, hasta, notas=nota)

    @api.model
    def _calc_usd_sin_recibir(self, parametro, fecha=None):
        """Facturas en USD de mercancia aun no recibida.

        Es exposicion cambiaria sobre bobina que todavia no esta en Santiago:
        si el peso se deprecia, se paga mas por producto que no se tiene.
        """
        _desde, hasta = self._rango_mes(fecha)
        usd = self.env["res.currency"].search([("name", "=", "USD")], limit=1)
        if not usd:
            return False
        facturas = self.env["account.move"].search([
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("currency_id", "=", usd.id),
            ("invoice_date", "<=", hasta),
            ("payment_state", "!=", "paid"),
        ])
        total = 0.0
        n = 0
        for f in facturas:
            ocs = f.invoice_line_ids.mapped("purchase_line_id.order_id")
            if any(o.ags_tiene_pendiente for o in ocs):
                # amount_residual de account.move ya viene en la moneda
                # del documento; amount_residual_currency solo existe en las
                # lineas. Como el dominio filtra currency_id = USD, el
                # importe que se suma esta en dolares.
                total += abs(f.amount_residual or 0.0)
                n += 1
        nota = "Facturas con mercancia pendiente: %s" % n
        return self._registrar(parametro, total, hasta, notas=nota)


    # ==================================================================
    # ANALISIS FINANCIERO - RATIOS DE BALANCE
    #
    # DIFERENCIA CLAVE CON EL RESTO DEL MODULO: los ratios de balance son
    # foto a una fecha y acumulan desde el inicio de operaciones. Los del
    # estado de resultados son de periodo. Mezclarlos sin cuidado produce
    # numeros que parecen validos y no lo son.
    #
    # Marco propuesto por la auditoria externa en agosto 2026.
    # ==================================================================

    @api.model
    def _saldo_balance(self, cuentas, hasta, invertir=False):
        """Saldo ACUMULADO de cuentas de balance hasta una fecha.

        A diferencia de _saldo_cuentas, que suma un periodo, aqui se acumula
        desde el inicio: el saldo de una cuenta de activo o pasivo es su
        historia completa, no el movimiento del mes.
        """
        if not cuentas:
            return 0.0
        grupos = self.env["account.move.line"]._read_group(
            [("account_id", "in", cuentas.ids),
             ("date", "<=", hasta),
             ("parent_state", "=", "posted")],
            aggregates=["balance:sum"],
        )
        total = (grupos[0][0] if grupos else 0.0) or 0.0
        return -total if invertir else total

    @api.model
    def _resultado_acumulado(self, hasta):
        """Resultado del ejercicio en curso, aun no cerrado a patrimonio.

        Odoo no traslada el resultado a patrimonio hasta el cierre anual, de
        modo que el patrimonio contable subestima el real durante el año.
        Para que los ratios de solvencia no queden distorsionados hay que
        sumarlo explicitamente.
        """
        cfg = self._config()
        inicio = hasta.replace(month=1, day=1)
        ingresos = self._saldo_cuentas(
            cfg._cuentas_por_tipo(["income", "income_other"]), inicio, hasta, invertir=True)
        gastos = self._saldo_cuentas(
            cfg._cuentas_por_tipo(["expense", "expense_direct_cost", "expense_depreciation"]),
            inicio, hasta)
        return ingresos - gastos

    @api.model
    def _patrimonio(self, hasta):
        cfg = self._config()
        base = self._saldo_balance(cfg.cuentas_patrimonio(), hasta, invertir=True)
        return base + self._resultado_acumulado(hasta)

    @api.model
    def _deuda_financiera(self, hasta):
        """Deuda con bancos, cooperativas y prestamistas.

        Si no hay cuentas declaradas se deriva de los terceros clasificados
        como acreedor financiero, que es el trabajo hecho al separar la CxP
        comercial de la financiera.
        """
        cfg = self._config()
        if cfg.cuenta_deuda_financiera_ids:
            return self._saldo_balance(cfg.cuenta_deuda_financiera_ids, hasta, invertir=True)
        lineas = self.env["account.move.line"].search([
            ("account_id.account_type", "in", ["liability_payable", "liability_current",
                                                "liability_non_current"]),
            ("partner_id.ags_tipo_acreedor", "=", "financiero"),
            ("date", "<=", hasta),
            ("parent_state", "=", "posted"),
        ])
        return -sum(lineas.mapped("balance"))

    # ---------- LIQUIDEZ ----------

    @api.model
    def _calc_razon_corriente(self, parametro, fecha=None):
        """Activo circulante / pasivo corriente."""
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        act = self._saldo_balance(cfg.cuentas_activo_circulante(), hasta)
        pas = self._saldo_balance(cfg.cuentas_pasivo_corriente(), hasta, invertir=True)
        if not pas:
            return False
        nota = "Activo circulante: %s | Pasivo corriente: %s" % (
            round(act, 2), round(pas, 2))
        return self._registrar(parametro, act / pas, hasta, notas=nota)

    @api.model
    def _calc_prueba_acida(self, parametro, fecha=None):
        """(Activo circulante - inventario) / pasivo corriente."""
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        act = self._saldo_balance(cfg.cuentas_activo_circulante(), hasta)
        inv = self._saldo_balance(cfg.cuenta_inventario_ids, hasta)
        pas = self._saldo_balance(cfg.cuentas_pasivo_corriente(), hasta, invertir=True)
        if not pas:
            return False
        nota = "Circulante: %s | Inventario: %s | Pasivo corriente: %s" % (
            round(act, 2), round(inv, 2), round(pas, 2))
        return self._registrar(parametro, (act - inv) / pas, hasta, notas=nota)

    @api.model
    def _calc_razon_efectivo(self, parametro, fecha=None):
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        efe = self._saldo_balance(cfg.cuentas_efectivo(), hasta)
        pas = self._saldo_balance(cfg.cuentas_pasivo_corriente(), hasta, invertir=True)
        if not pas:
            return False
        nota = "Efectivo: %s | Pasivo corriente: %s" % (round(efe, 2), round(pas, 2))
        return self._registrar(parametro, efe / pas, hasta, notas=nota)

    @api.model
    def _calc_capital_trabajo(self, parametro, fecha=None):
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        act = self._saldo_balance(cfg.cuentas_activo_circulante(), hasta)
        pas = self._saldo_balance(cfg.cuentas_pasivo_corriente(), hasta, invertir=True)
        nota = "Circulante: %s | Pasivo corriente: %s" % (round(act, 2), round(pas, 2))
        return self._registrar(parametro, act - pas, hasta, notas=nota)

    # ---------- ENDEUDAMIENTO Y SOLVENCIA ----------

    @api.model
    def _calc_endeudamiento_total(self, parametro, fecha=None):
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        pas = self._saldo_balance(cfg.cuentas_pasivo_total(), hasta, invertir=True)
        act = self._saldo_balance(cfg.cuentas_activo_total(), hasta)
        if not act:
            return False
        nota = "Pasivo total: %s | Activo total: %s" % (round(pas, 2), round(act, 2))
        return self._registrar(parametro, (pas / act) * 100.0, hasta, notas=nota)

    @api.model
    def _calc_pasivo_patrimonio(self, parametro, fecha=None):
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        pas = self._saldo_balance(cfg.cuentas_pasivo_total(), hasta, invertir=True)
        pat = self._patrimonio(hasta)
        if not pat:
            return False
        nota = "Pasivo: %s | Patrimonio: %s" % (round(pas, 2), round(pat, 2))
        return self._registrar(parametro, pas / pat, hasta, notas=nota)

    @api.model
    def _calc_patrimonio_activo(self, parametro, fecha=None):
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        pat = self._patrimonio(hasta)
        act = self._saldo_balance(cfg.cuentas_activo_total(), hasta)
        if not act:
            return False
        nota = "Patrimonio: %s | Activo: %s" % (round(pat, 2), round(act, 2))
        return self._registrar(parametro, (pat / act) * 100.0, hasta, notas=nota)

    @api.model
    def _calc_deuda_financiera(self, parametro, fecha=None):
        _d, hasta = self._rango_mes(fecha)
        deuda = self._deuda_financiera(hasta)
        cfg = self._config()
        origen = "cuentas declaradas" if cfg.cuenta_deuda_financiera_ids \
            else "terceros clasificados como financieros"
        return self._registrar(parametro, deuda, hasta, notas="Origen: %s" % origen)

    @api.model
    def _calc_deuda_patrimonio(self, parametro, fecha=None):
        _d, hasta = self._rango_mes(fecha)
        deuda = self._deuda_financiera(hasta)
        pat = self._patrimonio(hasta)
        if not pat:
            return False
        nota = "Deuda financiera: %s | Patrimonio: %s" % (round(deuda, 2), round(pat, 2))
        return self._registrar(parametro, deuda / pat, hasta, notas=nota)

    @api.model
    def _calc_cobertura_intereses(self, parametro, fecha=None):
        """Resultado operacional / gastos financieros del periodo.

        Mide cuantas veces la operacion cubre el costo de la deuda. Es el
        ratio que un banco revisa primero antes de otorgar o renovar linea.
        """
        cfg = self._config()
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        costo = self._costo_ventas(desde, hasta)
        gasto = self._saldo_cuentas(cfg.cuentas_gasto_operativo(), desde, hasta)
        operacional = ventas - costo - gasto
        financieros = self._saldo_cuentas(cfg.cuenta_gasto_financiero_ids, desde, hasta)
        if not financieros:
            return False
        nota = "Resultado operacional: %s | Gastos financieros: %s" % (
            round(operacional, 2), round(financieros, 2))
        return self._registrar(parametro, operacional / financieros, hasta, notas=nota)

    @api.model
    def _calc_deuda_ebitda(self, parametro, fecha=None):
        """Deuda financiera / EBITDA anualizado.

        El EBITDA se anualiza desde el acumulado del año en curso, no
        multiplicando el mes por doce: un mes puntual puede estar
        distorsionado por estacionalidad o cierres.
        """
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        inicio = hasta.replace(month=1, day=1)
        meses = hasta.month
        ventas = self._ventas_netas(inicio, hasta)
        costo = self._costo_ventas(inicio, hasta)
        gasto = self._saldo_cuentas(cfg.cuentas_gasto_operativo(), inicio, hasta)
        depre = self._saldo_cuentas(cfg.cuentas_depreciacion(), inicio, hasta)
        ebitda_ytd = ventas - costo - gasto
        ebitda_anual = (ebitda_ytd / meses) * 12 if meses else 0.0
        if not ebitda_anual:
            return False
        deuda = self._deuda_financiera(hasta)
        nota = ("Deuda: %s | EBITDA YTD (%s meses): %s | Anualizado: %s | "
                "Depreciacion YTD: %s") % (
            round(deuda, 2), meses, round(ebitda_ytd, 2),
            round(ebitda_anual, 2), round(depre, 2))
        return self._registrar(parametro, deuda / ebitda_anual, hasta, notas=nota)

    # ---------- RENTABILIDAD ----------

    @api.model
    def _calc_margen_operacional(self, parametro, fecha=None):
        cfg = self._config()
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        costo = self._costo_ventas(desde, hasta)
        gasto = self._saldo_cuentas(cfg.cuentas_gasto_operativo(), desde, hasta)
        depre = self._saldo_cuentas(cfg.cuentas_depreciacion(), desde, hasta)
        operacional = ventas - costo - gasto - depre
        nota = "Ventas: %s | Operacional: %s (incluye depreciacion %s)" % (
            round(ventas, 2), round(operacional, 2), round(depre, 2))
        return self._registrar(parametro, (operacional / ventas) * 100.0, hasta, notas=nota)

    @api.model
    def _calc_margen_neto(self, parametro, fecha=None):
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        cfg = self._config()
        ingresos = self._saldo_cuentas(
            cfg._cuentas_por_tipo(["income", "income_other"]), desde, hasta, invertir=True)
        gastos = self._saldo_cuentas(
            cfg._cuentas_por_tipo(["expense", "expense_direct_cost", "expense_depreciation"]),
            desde, hasta)
        neto = ingresos - gastos
        nota = "Ingresos totales: %s | Gastos totales: %s | Neto: %s" % (
            round(ingresos, 2), round(gastos, 2), round(neto, 2))
        return self._registrar(parametro, (neto / ventas) * 100.0, hasta, notas=nota)

    @api.model
    def _calc_roa(self, parametro, fecha=None):
        """Resultado neto anualizado / activo total."""
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        meses = hasta.month
        neto_ytd = self._resultado_acumulado(hasta)
        neto_anual = (neto_ytd / meses) * 12 if meses else 0.0
        act = self._saldo_balance(cfg.cuentas_activo_total(), hasta)
        if not act:
            return False
        nota = "Neto YTD: %s | Anualizado: %s | Activo: %s" % (
            round(neto_ytd, 2), round(neto_anual, 2), round(act, 2))
        return self._registrar(parametro, (neto_anual / act) * 100.0, hasta, notas=nota)

    @api.model
    def _calc_roe(self, parametro, fecha=None):
        """Resultado neto anualizado / patrimonio."""
        _d, hasta = self._rango_mes(fecha)
        meses = hasta.month
        neto_ytd = self._resultado_acumulado(hasta)
        neto_anual = (neto_ytd / meses) * 12 if meses else 0.0
        pat = self._patrimonio(hasta)
        if not pat:
            return False
        nota = "Neto anualizado: %s | Patrimonio: %s" % (
            round(neto_anual, 2), round(pat, 2))
        return self._registrar(parametro, (neto_anual / pat) * 100.0, hasta, notas=nota)

    # ---------- ACTIVIDAD ----------

    @api.model
    def _calc_dio(self, parametro, fecha=None):
        """Dias de inventario: (inventario / costo de ventas) x dias."""
        cfg = self._config()
        desde, hasta = self._rango_mes(fecha)
        inv = self._saldo_balance(cfg.cuenta_inventario_ids, hasta)
        costo = self._costo_ventas(desde, hasta)
        if not costo:
            return False
        dias = (hasta - desde).days + 1
        nota = "Inventario: %s | Costo de ventas: %s | Dias: %s" % (
            round(inv, 2), round(costo, 2), dias)
        return self._registrar(parametro, (inv / costo) * dias, hasta, notas=nota)

    @api.model
    def _calc_dpo(self, parametro, fecha=None):
        """Dias de pago: (CxP comercial / compras del periodo) x dias.

        Se usa solo la CxP comercial: incluir deuda financiera daria un DPO
        inflado que no refleja el credito de proveedores.
        """
        cfg = self._config()
        desde, hasta = self._rango_mes(fecha)
        lineas = self.env["account.move.line"].search([
            ("account_id.account_type", "=", "liability_payable"),
            ("date", "<=", hasta),
            ("parent_state", "=", "posted"),
            ("full_reconcile_id", "=", False),
            ("partner_id.ags_tipo_acreedor", "in", [False, "comercial"]),
        ])
        saldo = -sum(lineas.mapped("amount_residual"))
        costo = self._costo_ventas(desde, hasta)
        if not costo:
            return False
        dias = (hasta - desde).days + 1
        nota = "CxP comercial: %s | Costo de ventas: %s" % (
            round(saldo, 2), round(costo, 2))
        return self._registrar(parametro, (saldo / costo) * dias, hasta, notas=nota)

    @api.model
    def _calc_ccc(self, parametro, fecha=None):
        """Ciclo de conversion de efectivo: DIO + DSO - DPO."""
        _d, hasta = self._rango_mes(fecha)
        Param = self.env["ags.parametro"]
        Med = self.env["ags.medicion"]
        vals = {}
        for cod in ("DIO", "DSO", "DPO"):
            p = Param.search([("codigo", "=", cod)], limit=1)
            m = Med.search([("parametro_id", "=", p.id),
                            ("fecha_periodo", "=", hasta)], limit=1) if p else None
            if not m:
                return False
            vals[cod] = m.valor
        ccc = vals["DIO"] + vals["DSO"] - vals["DPO"]
        nota = "DIO %s + DSO %s - DPO %s" % (
            round(vals["DIO"], 1), round(vals["DSO"], 1), round(vals["DPO"], 1))
        return self._registrar(parametro, ccc, hasta, notas=nota)

    @api.model
    def _calc_rotacion_activos(self, parametro, fecha=None):
        """Ventas anualizadas / activo total."""
        cfg = self._config()
        _d, hasta = self._rango_mes(fecha)
        inicio = hasta.replace(month=1, day=1)
        meses = hasta.month
        ventas_ytd = self._ventas_netas(inicio, hasta)
        ventas_anual = (ventas_ytd / meses) * 12 if meses else 0.0
        act = self._saldo_balance(cfg.cuentas_activo_total(), hasta)
        if not act:
            return False
        nota = "Ventas anualizadas: %s | Activo: %s" % (
            round(ventas_anual, 2), round(act, 2))
        return self._registrar(parametro, ventas_anual / act, hasta, notas=nota)

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
                # SAVEPOINT POR PARAMETRO, NO SOLO try/except.
                #
                # Atrapar la excepcion de Python no basta: si el fallo vino de
                # una consulta invalida, Postgres deja la transaccion abortada
                # y TODA consulta posterior responde "current transaction is
                # aborted". Un unico calculador roto tumbaba la corrida
                # completa y el log se llenaba de errores en cascada que
                # escondian cual habia sido el primero.
                with self.env.cr.savepoint():
                    res = metodo(param, fecha)
                if res:
                    resultados["ok"].append(param.codigo)
                else:
                    resultados["sin_datos"].append(param.codigo)
            except Exception as e:
                _logger.exception("Error calculando %s", param.codigo)
                resultados["error"].append((param.codigo, str(e)))
        if resultados["error"]:
            _logger.warning(
                "Corrida con %s errores: %s", len(resultados["error"]),
                ", ".join(c for c, _m in resultados["error"]))
        return resultados
