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
        """Movimientos de consumo de MP y empaque en produccion."""
        cfg = self._config()
        categorias = cfg.categoria_mp_ids | cfg.categoria_empaque_ids
        if not categorias:
            return self.env["stock.move"], 0.0
        movimientos = self.env["stock.move"].search([
            ("state", "=", "done"),
            ("date", ">=", desde),
            ("date", "<=", hasta),
            ("raw_material_production_id", "!=", False),
            ("product_id.categ_id", "child_of", categorias.ids),
        ])
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
        """DSO estandar: (CxC / ventas del periodo) * dias del periodo.

        Se usa la formula de balance y no el promedio de dias de cobro porque
        esta ultima solo mira facturas ya cobradas: los clientes lentos, que
        son justamente el problema, quedan fuera del promedio hasta que pagan.
        """
        desde, hasta = self._rango_mes(fecha)
        ventas = self._ventas_netas(desde, hasta)
        if not ventas:
            return False
        cxc = self.env["account.move.line"]._read_group(
            [("account_id.account_type", "=", "asset_receivable"),
             ("date", "<=", hasta), ("parent_state", "=", "posted"),
             ("full_reconcile_id", "=", False)],
            aggregates=["amount_residual:sum"],
        )
        saldo = (cxc[0][0] if cxc else 0.0) or 0.0
        dias = (hasta - desde).days + 1
        valor = (saldo / ventas) * dias
        nota = "CxC pendiente: %s | Ventas: %s | Dias: %s" % (
            round(saldo, 2), round(ventas, 2), dias)
        return self._registrar(parametro, valor, hasta, notas=nota)

    @api.model
    def _calc_desviacion_plazo(self, parametro, fecha=None):
        """Dias reales de cobro menos dias pactados, ponderado por monto.

        Es mas util que el DSO absoluto: un DSO de 45 dias es excelente si el
        canal pacta 60 y pesimo si pacta 30. Lo accionable es la desviacion
        contra el compromiso, no el nivel.
        """
        desde, hasta = self._rango_mes(fecha)
        facturas = self.env["account.move"].search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
        ])
        peso = suma = 0.0
        n = 0
        for f in facturas:
            cobro = self._fecha_cobro(f)
            if not cobro or not f.invoice_date_due:
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
        nota = "Facturas cobradas evaluadas: %s | Ponderado por monto" % n
        return self._registrar(parametro, valor, hasta, notas=nota)

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
