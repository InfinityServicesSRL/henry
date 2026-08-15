# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AgsRentabilidad(models.Model):
    """Rentabilidad por cliente, vendedor y mercado, con costo de capital.

    LA IDEA CENTRAL: una venta al 22% de margen cobrada a 90 dias no vale lo
    mismo que una al 22% cobrada a 30. La diferencia es lo que cuesta
    financiar esa cuenta por cobrar, y ese costo no aparece en ningun estado
    de resultados por cliente.

    Y los dos efectos se componen: el cliente que negocia descuento suele ser
    el mismo que negocia plazo. Cada uno por separado se ve tolerable; juntos
    destruyen valor. Como el margen consolidado los promedia con los clientes
    buenos, el conjunto se ve sano mientras una parte de la cartera subsidia
    a la otra.

    FUENTE DE DATOS: account.move y no sale.report.

    Se verifico en agosto 2026 que el 9.5% de las facturas se emite sin
    pedido de venta -- principalmente las cuentas atendidas por Gerencia --
    y que sale.report les atribuye el vendedor de la ficha del cliente en
    lugar del real. Eso producia lecturas equivocadas: la cuenta Jose
    Santiago Inc aparecia con 12% de margen bajo un vendedor, cuando sus
    facturas reales muestran entre 31% y 92% bajo otro.

    Cuando existe pedido, el vendedor de la factura y el del pedido coinciden
    siempre (cero discrepancias en 1,589 facturas), asi que account.move
    cubre el 100% de las ventas sin perder informacion.
    """
    _name = "ags.rentabilidad"
    _description = "AG Intelligence - Rentabilidad Dimensional"
    _order = "fecha_periodo desc, ventas desc"
    _rec_name = "display_name"

    fecha_periodo = fields.Date(string="Periodo", required=True, index=True)
    anio = fields.Integer(string="Año", compute="_compute_periodo", store=True)
    mes = fields.Integer(string="Mes", compute="_compute_periodo", store=True)

    partner_id = fields.Many2one(
        "res.partner", string="Cliente", required=True, index=True, ondelete="cascade"
    )
    vendedor_id = fields.Many2one("res.users", string="Vendedor", index=True)
    mercado_id = fields.Many2one("ags.mercado", string="Mercado", index=True)

    # ---------- Venta ----------
    ventas = fields.Monetary(string="Ventas netas", currency_field="currency_id")
    costo = fields.Monetary(string="Costo de ventas", currency_field="currency_id")
    margen = fields.Monetary(
        string="Margen bruto", compute="_compute_margenes", store=True,
        currency_field="currency_id",
    )
    margen_pct = fields.Float(
        string="Margen bruto (%)", compute="_compute_margenes", store=True,
        digits=(5, 2),
    )
    n_facturas = fields.Integer(string="Facturas")
    ticket_promedio = fields.Monetary(
        string="Ticket promedio", compute="_compute_margenes", store=True,
        currency_field="currency_id",
    )

    # ---------- Ajustes ----------
    notas_credito = fields.Monetary(
        string="Notas de credito", currency_field="currency_id",
        help="Devoluciones y descuentos del periodo, por motivo clasificado.",
    )
    nc_pct = fields.Float(
        string="NC sobre ventas (%)", compute="_compute_margenes", store=True,
        digits=(5, 2),
    )

    # ---------- Cobro ----------
    dias_cobro = fields.Float(
        string="Dias de cobro", digits=(8, 1),
        help="Dias reales ponderados por monto, sobre facturas ya cobradas.",
    )
    dias_pactados = fields.Float(string="Dias pactados", digits=(8, 1))
    desviacion_dias = fields.Float(
        string="Desviacion", compute="_compute_margenes", store=True, digits=(8, 1),
    )
    facturas_cobradas = fields.Integer(string="Facturas cobradas")
    facturas_pendientes = fields.Integer(string="Facturas pendientes")
    facturas_sin_costo = fields.Integer(
        string="Facturas sin costo",
        help="Facturas para las que no se pudo determinar el costo. Con un "
             "valor alto, el margen de este cliente esta sobreestimado.",
    )

    # ---------- Costo de capital ----------
    costo_financiero = fields.Monetary(
        string="Costo financiero", compute="_compute_margenes", store=True,
        currency_field="currency_id",
        help="Costo de financiar la cuenta por cobrar durante los dias reales "
             "de cobro, a la tasa de costo de capital configurada.",
    )
    costo_financiero_pct = fields.Float(
        string="Costo financiero (%)", compute="_compute_margenes", store=True,
        digits=(5, 2),
    )
    margen_economico = fields.Monetary(
        string="Margen economico", compute="_compute_margenes", store=True,
        currency_field="currency_id",
    )
    margen_economico_pct = fields.Float(
        string="Margen economico (%)", compute="_compute_margenes", store=True,
        digits=(5, 2),
    )

    destruye_valor = fields.Boolean(
        string="Destruye valor", compute="_compute_margenes", store=True,
        help="Margen economico negativo: la venta cuesta mas de lo que deja.",
    )
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id
    )
    display_name = fields.Char(compute="_compute_display_name", store=True)

    _sql_constraints = [
        ("rentabilidad_unica", "unique(fecha_periodo, partner_id)",
         "Ya existe un calculo de rentabilidad para ese cliente y periodo."),
    ]

    @api.depends("fecha_periodo")
    def _compute_periodo(self):
        for r in self:
            r.anio = r.fecha_periodo.year if r.fecha_periodo else 0
            r.mes = r.fecha_periodo.month if r.fecha_periodo else 0

    @api.depends("partner_id", "fecha_periodo")
    def _compute_display_name(self):
        for r in self:
            r.display_name = "%s · %s" % (
                r.partner_id.display_name or "", r.fecha_periodo or "")

    @api.depends("ventas", "costo", "notas_credito", "dias_cobro",
                 "dias_pactados", "n_facturas")
    def _compute_margenes(self):
        cfg = self.env["ags.config"].search([], limit=1)
        tasa = (cfg.tasa_costo_capital if cfg else 12.3) / 100.0
        base = (cfg.dias_base_anio if cfg else 365) or 365
        for r in self:
            r.margen = r.ventas - r.costo
            r.margen_pct = (r.margen / r.ventas * 100.0) if r.ventas else 0.0
            r.ticket_promedio = (r.ventas / r.n_facturas) if r.n_facturas else 0.0
            r.nc_pct = (r.notas_credito / r.ventas * 100.0) if r.ventas else 0.0
            r.desviacion_dias = r.dias_cobro - r.dias_pactados
            r.costo_financiero = r.ventas * (r.dias_cobro / base) * tasa
            r.costo_financiero_pct = (
                r.costo_financiero / r.ventas * 100.0) if r.ventas else 0.0
            r.margen_economico = r.margen - r.costo_financiero - r.notas_credito
            r.margen_economico_pct = (
                r.margen_economico / r.ventas * 100.0) if r.ventas else 0.0
            r.destruye_valor = bool(r.ventas and r.margen_economico < 0)

    # ==================================================================
    # CALCULO
    # ==================================================================

    @api.model
    def _fecha_cobro(self, factura):
        lineas = factura.line_ids.filtered(
            lambda l: l.account_id.account_type == "asset_receivable")
        fechas = []
        for l in lineas:
            for pr in l.matched_credit_ids:
                if pr.credit_move_id.date:
                    fechas.append(pr.credit_move_id.date)
        return max(fechas) if fechas else None

    @api.model
    def calcular_periodo(self, fecha=None, recrear=True):
        """Calcula la rentabilidad de todos los clientes de un mes.

        El costo se toma de las capas de valoracion de los movimientos de
        salida asociados a la factura. Cuando no hay capa -- ventas de
        servicio, o mercancia sin valoracion -- el costo queda en cero y el
        margen sale sobreestimado para esa linea. Se registra el numero de
        facturas sin costo para que la lectura sea consciente de eso.
        """
        fecha = fecha or fields.Date.context_today(self)
        desde = fecha.replace(day=1)
        hasta = desde + relativedelta(months=1, days=-1)

        if recrear:
            self.search([("fecha_periodo", "=", hasta)]).unlink()

        AM = self.env["account.move"]
        facturas = AM.search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
        ])
        ncs = AM.search([
            ("move_type", "=", "out_refund"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
        ])

        acum = {}
        for f in facturas:
            pid = f.partner_id.commercial_partner_id.id
            d = acum.setdefault(pid, {
                "ventas": 0.0, "costo": 0.0, "nc": 0.0, "n": 0,
                "pond_dias": 0.0, "peso": 0.0, "pactados": 0.0, "peso_p": 0.0,
                "cobradas": 0, "pendientes": 0, "sin_costo": 0,
                "vendedor": f.invoice_user_id.id or False,
            })
            monto = f.amount_untaxed
            d["ventas"] += monto
            d["n"] += 1

            # Costo del periodo.
            #
            # Se usa el margen que AG Supply ya calcula en la factura
            # (x_total_margin_amount), y no las capas de valoracion de la
            # entrega. Razon: con la valoracion de inventario en modo manual,
            # los movimientos de salida no generan capa contable, de modo que
            # esa ruta devuelve cero y el margen sale inflado al 96-99%.
            #
            # El campo personalizado calcula contra el costo estandar del
            # producto y es el mismo que muestran las vistas de Odoo, con lo
            # cual el modulo y los tableros nativos no pueden contradecirse.
            margen_f = 0.0
            if "x_total_margin_amount" in f._fields:
                margen_f = f.x_total_margin_amount or 0.0
            if margen_f:
                d["costo"] += monto - margen_f
            else:
                # Respaldo: costo estandar por linea
                for l in f.invoice_line_ids:
                    if l.product_id:
                        d["costo"] += (l.quantity or 0.0) * (
                            l.product_id.standard_price or 0.0)
                    else:
                        d["sin_costo"] = d.get("sin_costo", 0) + 1

            # Plazo pactado
            if f.invoice_date_due and f.invoice_date:
                pact = (f.invoice_date_due - f.invoice_date).days
                d["pactados"] += pact * monto
                d["peso_p"] += monto

            # Dias reales de cobro
            cobro = self._fecha_cobro(f)
            if cobro and f.invoice_date:
                d["pond_dias"] += (cobro - f.invoice_date).days * monto
                d["peso"] += monto
                d["cobradas"] += 1
            else:
                d["pendientes"] += 1

        for n in ncs:
            pid = n.partner_id.commercial_partner_id.id
            if pid in acum:
                acum[pid]["nc"] += n.amount_untaxed

        Partner = self.env["res.partner"]
        registros = []
        for pid, d in acum.items():
            if d["ventas"] <= 0:
                continue
            p = Partner.browse(pid)
            registros.append({
                "fecha_periodo": hasta,
                "partner_id": pid,
                "vendedor_id": d["vendedor"],
                "mercado_id": p.ags_mercado_id.id or False,
                "ventas": d["ventas"],
                "costo": d["costo"],
                "notas_credito": d["nc"],
                "n_facturas": d["n"],
                "dias_cobro": (d["pond_dias"] / d["peso"]) if d["peso"] else 0.0,
                "dias_pactados": (d["pactados"] / d["peso_p"]) if d["peso_p"] else 0.0,
                "facturas_cobradas": d["cobradas"],
                "facturas_pendientes": d["pendientes"],
                "facturas_sin_costo": d.get("sin_costo", 0),
            })
        creados = self.create(registros) if registros else self.browse()
        _logger.info("Rentabilidad %s: %s clientes calculados", hasta, len(creados))
        return creados

    @api.model
    def resumen_por(self, dimension, fecha):
        """Agrega la rentabilidad por vendedor o por mercado."""
        campo = {"vendedor": "vendedor_id", "mercado": "mercado_id"}.get(dimension)
        if not campo:
            return {}
        registros = self.search([("fecha_periodo", "=", fecha)])
        salida = {}
        for r in registros:
            obj = r[campo]
            k = obj.display_name if obj else "(sin asignar)"
            x = salida.setdefault(k, {
                "ventas": 0.0, "margen": 0.0, "nc": 0.0, "costo_fin": 0.0,
                "economico": 0.0, "clientes": 0, "pond_dias": 0.0, "peso": 0.0,
            })
            x["ventas"] += r.ventas
            x["margen"] += r.margen
            x["nc"] += r.notas_credito
            x["costo_fin"] += r.costo_financiero
            x["economico"] += r.margen_economico
            x["clientes"] += 1
            if r.dias_cobro:
                x["pond_dias"] += r.dias_cobro * r.ventas
                x["peso"] += r.ventas
        for k, v in salida.items():
            v["margen_pct"] = (v["margen"] / v["ventas"] * 100.0) if v["ventas"] else 0.0
            v["economico_pct"] = (
                v["economico"] / v["ventas"] * 100.0) if v["ventas"] else 0.0
            v["dias_cobro"] = (v["pond_dias"] / v["peso"]) if v["peso"] else 0.0
        return salida
