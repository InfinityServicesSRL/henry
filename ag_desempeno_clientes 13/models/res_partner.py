# -*- coding: utf-8 -*-
"""Desempeño de clientes — cálculo y campos sobre res.partner.

Decisiones de diseño que conviene conocer antes de tocar este archivo:

1. **Los campos son almacenados y NO computados en tiempo real.** Calcular
   doce meses de facturación, conciliación y despacho para 544 clientes en
   cada lectura haría inusable la vista de contactos. El recálculo corre por
   cron (diario de madrugada) o a mano desde el botón de la ficha.

2. **Todo se consolida por matriz** (``commercial_partner_id``): las
   sucursales suman al cliente comercial, que es quien tiene el crédito y con
   quien existe la relación. Sólo los partners comerciales llevan cifras.

3. **Las agregaciones pesadas van en SQL.** El ORM haría cientos de queries
   por cliente; una consulta agrupada resuelve los 544 de una vez.

4. **La normalización es por percentil dentro del segmento**
   (``ag_segmento_categoria``). Un almacenista de ticket bajo y alta
   frecuencia no compite contra un hotel: compite contra otros almacenistas.
"""

import json
import logging
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

GRADOS = [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D"), ("nuevo", "Nuevo")]
TENDENCIAS = [
    ("subida_fuerte", "▲▲ Crecimiento fuerte"),
    ("subida", "▲ Mejora"),
    ("estable", "► Estable"),
    ("baja", "▼ Deterioro"),
    ("baja_fuerte", "▼▼ Deterioro grave"),
    ("divergente", "⚠ Divergente"),
    ("sin_historia", "· Sin historia"),
]
# Tramos de recencia. `ag_dias_sin_comprar` es un entero suelto: agrupar un
# gráfico por él produce una columna por valor distinto y no se lee. El tramo
# es el mismo dato en la forma en que se mira.
RECENCIA = [
    ("r0", "0–30 días"),
    ("r1", "31–60 días"),
    ("r2", "61–90 días"),
    ("r3", "Más de 90 días"),
    ("nunca", "Nunca compró"),
]
# Un despacho validado dentro de esta ventana desde la orden es mostrador /
# retiro en planta, no una entrega programada. Promediar ambos borra la señal.
HORAS_DESPACHO_INMEDIATO = 4


class ResPartner(models.Model):
    _inherit = "res.partner"

    # ------------------------------------------------------------------
    # Evaluación
    # ------------------------------------------------------------------
    ag_desemp_score = fields.Float("Score", digits=(16, 1), readonly=True, index=True)
    ag_desemp_grado = fields.Selection(GRADOS, "Grado", readonly=True, index=True)
    ag_desemp_tendencia = fields.Selection(TENDENCIAS, "Tendencia", readonly=True)
    ag_desemp_fecha = fields.Datetime("Último cálculo", readonly=True)
    ag_desemp_evaluable = fields.Boolean(
        "Evaluable", readonly=True,
        help="Cliente comercial con historia suficiente para recibir grado.")

    ag_sc_valor = fields.Float("Sub-score valor", digits=(16, 1), readonly=True)
    ag_sc_rentabilidad = fields.Float("Sub-score rentabilidad", digits=(16, 1), readonly=True)
    ag_sc_pago = fields.Float("Sub-score pago", digits=(16, 1), readonly=True)
    ag_sc_consistencia = fields.Float("Sub-score consistencia", digits=(16, 1), readonly=True)
    ag_sc_operativo = fields.Float("Sub-score operativo", digits=(16, 1), readonly=True)

    # ------------------------------------------------------------------
    # Compras
    # ------------------------------------------------------------------
    ag_venta_12m = fields.Monetary("Venta neta 12M", currency_field="currency_id", readonly=True)
    ag_margen_12m = fields.Monetary("Margen 12M", currency_field="currency_id", readonly=True)
    ag_margen_pct = fields.Float("Margen %", digits=(16, 2), readonly=True)
    ag_devoluciones_12m = fields.Monetary("Devoluciones 12M", currency_field="currency_id", readonly=True)
    ag_devoluciones_pct = fields.Float("Devoluciones %", digits=(16, 2), readonly=True)
    ag_facturas_12m = fields.Integer("Facturas 12M", readonly=True)
    ag_orden_promedio = fields.Monetary("Orden promedio", currency_field="currency_id", readonly=True)
    ag_meses_activos = fields.Integer("Meses activos de 12", readonly=True)
    ag_cv_compra = fields.Float(
        "Variabilidad de compra", digits=(16, 2), readonly=True,
        help="Coeficiente de variación mensual. Bajo = comprador predecible.")
    ag_skus_12m = fields.Integer("SKUs distintos 12M", readonly=True)
    ag_primera_compra = fields.Date("Primera compra", readonly=True)
    ag_ultima_compra = fields.Date("Última compra", readonly=True)
    ag_dias_sin_comprar = fields.Integer("Días sin comprar", readonly=True)
    ag_recencia = fields.Selection(
        RECENCIA, "Recencia", readonly=True, index=True,
        help="Tramo de días desde la última compra. Existe para poder agrupar: "
             "el número suelto no se puede graficar.")
    ag_sin_contacto = fields.Boolean(
        "Sin correo ni WhatsApp", readonly=True, index=True,
        help="No hay por dónde escribirle: ni correo, ni WhatsApp, ni teléfono. "
             "Marca el hueco que impide enviarle estado de cuenta o ficha.")
    ag_participacion_pct = fields.Float(
        "% de la venta total", digits=(16, 2), readonly=True,
        help="Peso del cliente en la facturación total de la compañía.")
    ag_venta_mensual = fields.Monetary(
        "Compra promedio mensual", currency_field="currency_id", readonly=True,
        help="Ventas netas de la ventana divididas entre sus meses. Es la cifra "
             "con la que el vendedor razona: nadie negocia en unidades de doce meses.")

    # ------------------------------------------------------------------
    # Potencial — lo estima quien visita, no lo calcula el ERP
    # ------------------------------------------------------------------
    ag_potencial_mes = fields.Monetary(
        "Potencial de compra / mes", currency_field="currency_id",
        help="Cuánto podría comprarnos este cliente al mes si le vendiéramos "
             "todo lo que consume. Lo estima el vendedor con lo que ve en el "
             "negocio; el ERP no puede saberlo porque las compras a la "
             "competencia no pasan por aquí.")
    ag_potencial_fecha = fields.Date("Potencial estimado el")
    ag_potencial_uid = fields.Many2one("res.users", "Potencial estimado por")
    ag_captura_pct = fields.Float(
        "% de captura", digits=(16, 2), readonly=True,
        help="Compra mensual real sobre el potencial estimado.")
    ag_brecha_mes = fields.Monetary(
        "Brecha mensual", currency_field="currency_id", readonly=True,
        help="Lo que el cliente puede comprar y no nos compra.")

    # ------------------------------------------------------------------
    # Logística de entrega
    # ------------------------------------------------------------------
    ag_costo_logistica = fields.Selection(
        [("4", "4% — larga distancia"),
         ("3", "3% — intermedia"),
         ("2", "2% — zona cercana")],
        string="Costo logística entrega",
        help="Costo de llevarle la mercancía, como porcentaje de la venta. "
             "Se sugiere por distancia pero se fija a mano: la ruta, el tipo "
             "de camión y la frecuencia pesan tanto como los kilómetros.")
    ag_km_planta = fields.Float(
        "Km desde planta Santiago", digits=(16, 1),
        help="Distancia por carretera entre la planta de Santiago y la "
             "dirección de entrega del cliente.")
    ag_costo_logistica_monto = fields.Monetary(
        "Costo logístico mensual", currency_field="currency_id", readonly=True,
        help="Compra promedio mensual por el porcentaje de logística.")
    ag_margen_neto_pct = fields.Float(
        "Margen neto de logística %", digits=(16, 2), readonly=True,
        help="Margen de contribución menos el costo de entrega. Un cliente "
             "lejano con margen apretado puede estar dando pérdida y el "
             "margen bruto no lo muestra.")

    # ------------------------------------------------------------------
    # Perfil cualitativo — lo que el ERP no puede calcular
    # ------------------------------------------------------------------
    ag_perfil_notas = fields.Text(
        "Lo que sabemos de este cliente",
        help="Cómo tratarlo: quién decide, cómo prefiere que le hablen, qué no "
             "tolera, cada cuánto quiere que lo visiten. Es lo único de esta "
             "ficha que no sale de un cálculo, y lo que evita que un vendedor "
             "nuevo llegue en frío.")
    ag_perfil_uid = fields.Many2one("res.users", "Perfil escrito por", readonly=True)
    ag_perfil_fecha = fields.Datetime("Perfil actualizado el", readonly=True)

    # ------------------------------------------------------------------
    # Tendencia
    # ------------------------------------------------------------------
    ag_venta_bloque = fields.Monetary("Venta bloque reciente", currency_field="currency_id", readonly=True)
    ag_venta_bloque_prev = fields.Monetary("Venta bloque anterior", currency_field="currency_id", readonly=True)
    ag_tend_velocidad = fields.Float(
        "Velocidad %", digits=(16, 2), readonly=True,
        help="Variación del bloque reciente contra el anterior.")
    ag_tend_direccion = fields.Float(
        "Dirección %", digits=(16, 2), readonly=True,
        help="Pendiente de la regresión de 12 meses, normalizada contra el "
             "promedio mensual. Dice hacia dónde va la relación completa, no "
             "sólo el último trimestre.")
    ag_serie_json = fields.Text(
        "Serie mensual", readonly=True,
        help="Serie de 12 meses en JSON, para los gráficos de la ficha y los reportes.")

    # ------------------------------------------------------------------
    # Cuentas por cobrar
    # ------------------------------------------------------------------
    ag_cxc_total = fields.Monetary("CxC total", currency_field="currency_id", readonly=True)
    ag_cxc_corriente = fields.Monetary("Corriente", currency_field="currency_id", readonly=True)
    ag_cxc_1_30 = fields.Monetary("Vencido 1–30", currency_field="currency_id", readonly=True)
    ag_cxc_31_60 = fields.Monetary("Vencido 31–60", currency_field="currency_id", readonly=True)
    ag_cxc_61_90 = fields.Monetary("Vencido 61–90", currency_field="currency_id", readonly=True)
    ag_cxc_90_mas = fields.Monetary("Vencido +90", currency_field="currency_id", readonly=True)
    ag_cxc_vencido = fields.Monetary("Total vencido", currency_field="currency_id", readonly=True)
    ag_cxc_vencido_pct = fields.Float("% vencido", digits=(16, 2), readonly=True)
    ag_dias_atraso_max = fields.Integer("Atraso máximo (días)", readonly=True)
    ag_dias_cobro_real = fields.Float(
        "Días de cobro reales", digits=(16, 1), readonly=True,
        help="Promedio ponderado por monto entre la fecha de factura y la "
             "fecha del pago que la concilió.")
    ag_dias_cobro_pactado = fields.Integer("Días pactados", readonly=True)
    ag_desviacion_pago = fields.Float(
        "Desviación de pago", digits=(16, 1), readonly=True,
        help="Días reales menos días pactados. Positivo = paga tarde.")
    ag_pct_a_tiempo = fields.Float("% facturas a tiempo", digits=(16, 2), readonly=True)
    ag_dso = fields.Float("DSO", digits=(16, 1), readonly=True)

    # ------------------------------------------------------------------
    # Crédito
    # ------------------------------------------------------------------
    ag_credito_disponible = fields.Monetary(
        "Crédito disponible", currency_field="currency_id", readonly=True,
        help="Límite menos CxC menos el backlog aún no facturado. Sin restar "
             "el backlog se autoriza despacho contra crédito ya comprometido.")
    ag_uso_credito_pct = fields.Float("% de crédito usado", digits=(16, 2), readonly=True)

    # ------------------------------------------------------------------
    # Pipeline y entregas
    # ------------------------------------------------------------------
    ag_cotiz_monto = fields.Monetary("Cotizaciones vivas", currency_field="currency_id", readonly=True)
    ag_cotiz_cant = fields.Integer("# cotizaciones vivas", readonly=True)
    ag_conversion_pct = fields.Float(
        "% de conversión", digits=(16, 2), readonly=True,
        help="Órdenes confirmadas sobre cotizaciones emitidas en la ventana.")
    ag_backlog_monto = fields.Monetary("Backlog por entregar", currency_field="currency_id", readonly=True)
    ag_backlog_cant = fields.Integer("# órdenes abiertas", readonly=True)
    ag_backlog_dias = fields.Integer("Antigüedad del backlog (días)", readonly=True)
    ag_sin_facturar_monto = fields.Monetary(
        "Entregado sin facturar", currency_field="currency_id", readonly=True)
    ag_entregas_12m = fields.Integer("Entregas 12M", readonly=True)
    ag_lead_time_prog = fields.Float(
        "Días de entrega programada", digits=(16, 1), readonly=True,
        help="Sólo despachos que no salieron el mismo momento de la orden. "
             "Mezclar mostrador con entrega programada borra la señal.")
    ag_despacho_inmediato_pct = fields.Float(
        "% despacho inmediato", digits=(16, 2), readonly=True)
    ag_pct_entregas_completas = fields.Float("% entregas completas", digits=(16, 2), readonly=True)

    # ------------------------------------------------------------------
    # Relacionados
    # ------------------------------------------------------------------
    ag_alerta_ids = fields.One2many(
        "ag.desempeno.alerta", "partner_id", string="Alertas",
        domain=[("activa", "=", True), ("atendida", "=", False)])
    ag_alerta_count = fields.Integer("Alertas abiertas", readonly=True, index=True)
    ag_alerta_critica_count = fields.Integer("Alertas críticas", readonly=True, index=True)
    ag_snapshot_ids = fields.One2many(
        "ag.desempeno.snapshot", "partner_id", string="Historial")
    ag_snapshot_count = fields.Integer("Snapshots", compute="_compute_ag_counts")

    # Enlace público del estado de cuenta
    ag_portal_token = fields.Char("Token público", copy=False, groups="base.group_user")
    ag_portal_expira = fields.Date("Enlace válido hasta", copy=False, groups="base.group_user")

    def _compute_ag_counts(self):
        snap = self.env["ag.desempeno.snapshot"]
        for p in self:
            p.ag_snapshot_count = snap.search_count([("partner_id", "=", p.id)])

    def _ag_refrescar_conteos(self):
        """Refresca los conteos de alertas tras evaluarlas."""
        Alerta = self.env["ag.desempeno.alerta"]
        for p in self:
            dom = [("partner_id", "=", p.id), ("activa", "=", True), ("atendida", "=", False)]
            p.write({
                "ag_alerta_count": Alerta.search_count(dom),
                "ag_alerta_critica_count": Alerta.search_count(
                    dom + [("severidad", "=", "crit")]),
            })

    # ==================================================================
    # Autoría de los campos que escribe una persona
    # ==================================================================
    def write(self, vals):
        """Deja constancia de quién estimó el potencial y quién escribió el perfil.

        Ambos son juicios de una persona, no cálculos: sin saber quién los puso
        y cuándo, nadie sabe si todavía valen.
        """
        ahora = fields.Datetime.now()
        hoy = fields.Date.context_today(self)
        if "ag_potencial_mes" in vals and "ag_potencial_uid" not in vals:
            vals["ag_potencial_uid"] = self.env.user.id
            vals["ag_potencial_fecha"] = hoy
        if "ag_perfil_notas" in vals and "ag_perfil_uid" not in vals:
            vals["ag_perfil_uid"] = self.env.user.id
            vals["ag_perfil_fecha"] = ahora
        return super().write(vals)

    @api.onchange("ag_km_planta")
    def _onchange_km_sugiere_logistica(self):
        """Sugiere el tramo de costo por distancia, sin imponerlo."""
        cfg = self.env["ag.desempeno.config"].get_config()
        for p in self:
            if not p.ag_km_planta:
                continue
            if p.ag_km_planta <= cfg.km_corta:
                p.ag_costo_logistica = "2"
            elif p.ag_km_planta <= cfg.km_media:
                p.ag_costo_logistica = "3"
            else:
                p.ag_costo_logistica = "4"

    # ==================================================================
    # Entrada pública
    # ==================================================================
    def action_recalcular_desempeno(self):
        """Botón de la ficha: recalcula este cliente (o su matriz)."""
        matrices = self.mapped("commercial_partner_id")
        matrices._ag_calcular_desempeno()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Desempeño actualizado"),
                "message": _("%s cliente(s) recalculado(s).") % len(matrices),
                "type": "success",
                "sticky": False,
            },
        }

    @api.model
    def _cron_recalcular_desempeno(self):
        """Cron diario. Recalcula toda la cartera comercial activa."""
        partners = self.search([
            ("customer_rank", ">", 0),
            ("active", "=", True),
        ])
        matrices = partners.mapped("commercial_partner_id")
        _logger.info("Desempeño de clientes: recalculando %s matrices", len(matrices))
        matrices._ag_calcular_desempeno()
        # La venta 12M por producto ordena la auditoría de fichas técnicas.
        # Va aquí y no en un cron propio: es una sola consulta agregada y no
        # justifica un segundo trabajo nocturno que alguien deba vigilar.
        self.env["product.template"]._ag_recalcular_ventas()
        return True

    @api.model
    def _cron_cerrar_mes(self):
        """Cron mensual. Congela el snapshot del mes que acaba de cerrar."""
        hoy = fields.Date.context_today(self)
        corte = date(hoy.year, hoy.month, 1) - timedelta(days=1)
        partners = self.search([("customer_rank", ">", 0), ("active", "=", True)])
        partners.mapped("commercial_partner_id")._ag_guardar_snapshot(corte)
        return True

    # ==================================================================
    # Motor de cálculo
    # ==================================================================
    def _ag_calcular_desempeno(self):
        """Recalcula las métricas y el score del recordset (matrices)."""
        if not self:
            return
        cfg = self.env["ag.desempeno.config"].get_config()
        hoy = fields.Date.context_today(self)

        # La ventana termina en el ÚLTIMO MES CERRADO, no en el mes en curso.
        # Si incluyera el mes corriente, cada día 2 toda la cartera aparecería
        # en caída libre contra un mes que apenas empezó: la tendencia diría
        # "deterioro" en todos, y una alerta que se dispara siempre no sirve.
        # La recencia, la CxC y el backlog sí miran el día de hoy.
        fin = date(hoy.year, hoy.month, 1) - timedelta(days=1)
        desde = date(fin.year, fin.month, 1) - relativedelta(months=cfg.meses_ventana - 1)

        matrices = self.filtered(lambda p: p.id == p.commercial_partner_id.id)
        if not matrices:
            matrices = self.mapped("commercial_partner_id")
        ids = matrices.ids

        ventas = self._ag_sql_ventas(ids, desde, fin)
        serie = self._ag_sql_serie_mensual(ids, desde, fin)
        cxc = self._ag_sql_cxc(ids, hoy)
        pagos = self._ag_sql_dias_pago(ids, desde, fin)
        pipeline = self._ag_sql_pipeline(ids, desde, hoy)
        entregas = self._ag_sql_entregas(ids, desde, fin)
        skus = self._ag_sql_skus(ids, desde, fin)

        total_compania = sum(v["venta"] for v in ventas.values()) or 1.0
        bruto = {}

        for p in matrices:
            v = ventas.get(p.id, {})
            s = serie.get(p.id, [0.0] * cfg.meses_ventana)
            c = cxc.get(p.id, {})
            pg = pagos.get(p.id, {})
            pl = pipeline.get(p.id, {})
            en = entregas.get(p.id, {})

            venta = v.get("venta", 0.0)
            margen = v.get("margen", 0.0)
            devol = abs(v.get("devoluciones", 0.0))
            facturas = v.get("facturas", 0)
            meses_activos = len([x for x in s if x > 0])

            vel, direc = self._ag_tendencia(s, cfg.meses_tendencia_corta)
            bloque, bloque_prev = self._ag_bloques(s, cfg.meses_tendencia_corta)

            dias_pactados = self._ag_dias_pactados(p)
            dias_reales = pg.get("dias", 0.0)
            cxc_total = c.get("total", 0.0)
            vencido = (c.get("b1", 0.0) + c.get("b2", 0.0)
                       + c.get("b3", 0.0) + c.get("b4", 0.0))

            limite = float(p.ag_limite_de_credito or p.credit_limit or 0.0)
            backlog = pl.get("backlog", 0.0)
            disponible = limite - cxc_total - backlog

            ultima = v.get("ultima")
            dias_sin = (hoy - ultima).days if ultima else 9999

            # El promedio se calcula sobre los meses de la ventana, no sobre
            # los meses con compra: un cliente que compró un mes de doce no
            # tiene un promedio mensual igual a esa compra.
            venta_mensual = venta / cfg.meses_ventana if cfg.meses_ventana else 0.0
            potencial = p.ag_potencial_mes or 0.0
            pct_log = float(p.ag_costo_logistica or 0.0)
            costo_log = venta_mensual * pct_log / 100.0
            margen_pct = (margen / venta * 100.0) if venta else 0.0

            vals = {
                "ag_venta_mensual": venta_mensual,
                "ag_captura_pct": (venta_mensual / potencial * 100.0) if potencial else 0.0,
                "ag_brecha_mes": max(potencial - venta_mensual, 0.0) if potencial else 0.0,
                "ag_costo_logistica_monto": costo_log,
                "ag_margen_neto_pct": margen_pct - pct_log,
                "ag_venta_12m": venta,
                "ag_margen_12m": margen,
                "ag_margen_pct": (margen / venta * 100.0) if venta else 0.0,
                "ag_devoluciones_12m": devol,
                "ag_devoluciones_pct": (devol / venta * 100.0) if venta else 0.0,
                "ag_facturas_12m": facturas,
                "ag_orden_promedio": (venta / facturas) if facturas else 0.0,
                "ag_meses_activos": meses_activos,
                "ag_cv_compra": self._ag_coef_variacion(s),
                "ag_skus_12m": skus.get(p.id, 0),
                "ag_primera_compra": v.get("primera"),
                "ag_ultima_compra": ultima,
                "ag_dias_sin_comprar": min(dias_sin, 9999),
                "ag_recencia": self._ag_recencia(ultima, dias_sin),
                "ag_sin_contacto": not (
                    p.email or p.ag_email_encargado or p.ag_email_contacto_compra
                    or p.ag_whatsapp_encargado or p.ag_whatsapp_contacto_compra
                    or p.mobile or p.phone),
                "ag_participacion_pct": venta / total_compania * 100.0,
                "ag_venta_bloque": bloque,
                "ag_venta_bloque_prev": bloque_prev,
                "ag_tend_velocidad": vel,
                "ag_tend_direccion": direc,
                "ag_serie_json": json.dumps(self._ag_serie_etiquetada(s, fin)),
                "ag_cxc_total": cxc_total,
                "ag_cxc_corriente": c.get("b0", 0.0),
                "ag_cxc_1_30": c.get("b1", 0.0),
                "ag_cxc_31_60": c.get("b2", 0.0),
                "ag_cxc_61_90": c.get("b3", 0.0),
                "ag_cxc_90_mas": c.get("b4", 0.0),
                "ag_cxc_vencido": vencido,
                "ag_cxc_vencido_pct": (vencido / cxc_total * 100.0) if cxc_total else 0.0,
                "ag_dias_atraso_max": int(c.get("atraso_max", 0)),
                "ag_dias_cobro_real": dias_reales,
                "ag_dias_cobro_pactado": dias_pactados,
                "ag_desviacion_pago": dias_reales - dias_pactados if dias_reales else 0.0,
                "ag_pct_a_tiempo": pg.get("pct_a_tiempo", 0.0),
                "ag_dso": (cxc_total / venta * 365.0) if venta else 0.0,
                "ag_credito_disponible": disponible,
                "ag_uso_credito_pct": ((cxc_total + backlog) / limite * 100.0) if limite else 0.0,
                "ag_cotiz_monto": pl.get("cotiz", 0.0),
                "ag_cotiz_cant": pl.get("cotiz_cant", 0),
                "ag_conversion_pct": pl.get("conversion", 0.0),
                "ag_backlog_monto": backlog,
                "ag_backlog_cant": pl.get("backlog_cant", 0),
                "ag_backlog_dias": int(pl.get("backlog_dias", 0)),
                "ag_sin_facturar_monto": pl.get("sin_facturar", 0.0),
                "ag_entregas_12m": en.get("entregas", 0),
                "ag_lead_time_prog": en.get("lead_prog", 0.0),
                "ag_despacho_inmediato_pct": en.get("pct_inmediato", 0.0),
                "ag_pct_entregas_completas": en.get("pct_completas", 0.0),
                "ag_desemp_fecha": fields.Datetime.now(),
                "ag_desemp_evaluable": meses_activos >= cfg.minimo_meses_para_score,
            }
            p.write(vals)
            bruto[p.id] = vals

        # El score es relativo: sólo se puede calcular cuando todas las
        # métricas del segmento están en la tabla.
        matrices._ag_puntuar(cfg, bruto)
        matrices._ag_evaluar_alertas(cfg)
        return True

    # ------------------------------------------------------------------
    # Consultas SQL
    # ------------------------------------------------------------------
    def _ag_sql_ventas(self, ids, desde, hasta):
        self.env.cr.execute("""
            SELECT m.commercial_partner_id AS pid,
                   SUM(CASE WHEN m.move_type = 'out_invoice'
                            THEN m.amount_untaxed_signed ELSE 0 END)
                 + SUM(CASE WHEN m.move_type = 'out_refund'
                            THEN m.amount_untaxed_signed ELSE 0 END) AS venta,
                   SUM(COALESCE(m.x_total_margin_amount, 0))          AS margen,
                   SUM(CASE WHEN m.move_type = 'out_refund'
                            THEN m.amount_untaxed_signed ELSE 0 END)  AS devoluciones,
                   COUNT(*) FILTER (WHERE m.move_type = 'out_invoice') AS facturas,
                   MIN(m.invoice_date) AS primera,
                   MAX(m.invoice_date) FILTER (WHERE m.move_type = 'out_invoice') AS ultima
              FROM account_move m
             WHERE m.commercial_partner_id IN %s
               AND m.move_type IN ('out_invoice', 'out_refund')
               AND m.state = 'posted'
               AND m.invoice_date >= %s AND m.invoice_date <= %s
          GROUP BY m.commercial_partner_id
        """, (tuple(ids), desde, hasta))
        return {r["pid"]: r for r in self.env.cr.dictfetchall()}

    def _ag_sql_serie_mensual(self, ids, desde, hasta):
        """Serie de venta neta por mes, alineada a la ventana completa.

        Los meses sin factura tienen que aparecer como cero, no faltar: un
        hueco es exactamente la señal que interesa detectar.
        """
        cfg = self.env["ag.desempeno.config"].get_config()
        self.env.cr.execute("""
            SELECT m.commercial_partner_id AS pid,
                   DATE_TRUNC('month', m.invoice_date)::date AS mes,
                   SUM(m.amount_untaxed_signed) AS venta
              FROM account_move m
             WHERE m.commercial_partner_id IN %s
               AND m.move_type IN ('out_invoice', 'out_refund')
               AND m.state = 'posted'
               AND m.invoice_date >= %s AND m.invoice_date <= %s
          GROUP BY 1, 2
        """, (tuple(ids), desde, hasta))
        crudo = {}
        for r in self.env.cr.dictfetchall():
            crudo.setdefault(r["pid"], {})[r["mes"]] = float(r["venta"] or 0.0)

        meses = []
        ancla = date(hasta.year, hasta.month, 1)
        for i in range(cfg.meses_ventana - 1, -1, -1):
            meses.append(ancla - relativedelta(months=i))

        return {pid: [max(vals.get(m, 0.0), 0.0) for m in meses]
                for pid, vals in crudo.items()}

    def _ag_sql_cxc(self, ids, hoy):
        self.env.cr.execute("""
            SELECT m.commercial_partner_id AS pid,
                   SUM(m.amount_residual)                                     AS total,
                   SUM(CASE WHEN COALESCE(m.invoice_date_due, m.invoice_date) >= %(hoy)s
                            THEN m.amount_residual ELSE 0 END)                AS b0,
                   SUM(CASE WHEN %(hoy)s - COALESCE(m.invoice_date_due, m.invoice_date)
                                 BETWEEN 1 AND 30
                            THEN m.amount_residual ELSE 0 END)                AS b1,
                   SUM(CASE WHEN %(hoy)s - COALESCE(m.invoice_date_due, m.invoice_date)
                                 BETWEEN 31 AND 60
                            THEN m.amount_residual ELSE 0 END)                AS b2,
                   SUM(CASE WHEN %(hoy)s - COALESCE(m.invoice_date_due, m.invoice_date)
                                 BETWEEN 61 AND 90
                            THEN m.amount_residual ELSE 0 END)                AS b3,
                   SUM(CASE WHEN %(hoy)s - COALESCE(m.invoice_date_due, m.invoice_date) > 90
                            THEN m.amount_residual ELSE 0 END)                AS b4,
                   MAX(%(hoy)s - COALESCE(m.invoice_date_due, m.invoice_date)) AS atraso_max
              FROM account_move m
             WHERE m.commercial_partner_id IN %(ids)s
               AND m.move_type = 'out_invoice'
               AND m.state = 'posted'
               AND m.payment_state IN ('not_paid', 'partial')
               AND m.amount_residual > 0
          GROUP BY m.commercial_partner_id
        """, {"ids": tuple(ids), "hoy": hoy})
        out = {}
        for r in self.env.cr.dictfetchall():
            r = {k: (float(v) if isinstance(v, (int, float)) and k != "pid" else v)
                 for k, v in r.items()}
            r["atraso_max"] = max(r.get("atraso_max") or 0, 0)
            out[r["pid"]] = r
        return out

    def _ag_sql_dias_pago(self, ids, desde, hasta):
        """Días reales entre factura y el pago que la concilió.

        Se pondera por monto: una factura de RD$700,000 pagada tarde pesa más
        que una de RD$5,000 pagada a tiempo. El promedio simple miente en
        carteras con tickets muy dispares.
        """
        self.env.cr.execute("""
            WITH conciliado AS (
                SELECT m.id                       AS move_id,
                       m.commercial_partner_id    AS pid,
                       m.invoice_date             AS f_factura,
                       m.invoice_date_due         AS f_vence,
                       m.amount_untaxed_signed    AS monto,
                       MAX(cl.date)               AS f_pago
                  FROM account_move m
                  JOIN account_move_line aml
                    ON aml.move_id = m.id
                  JOIN account_account acc
                    ON acc.id = aml.account_id
                   AND acc.account_type = 'asset_receivable'
                  JOIN account_partial_reconcile apr
                    ON apr.debit_move_id = aml.id
                  JOIN account_move_line cml
                    ON cml.id = apr.credit_move_id
                  JOIN account_move cl
                    ON cl.id = cml.move_id
                 WHERE m.commercial_partner_id IN %s
                   AND m.move_type = 'out_invoice'
                   AND m.state = 'posted'
                   AND m.invoice_date >= %s AND m.invoice_date <= %s
              GROUP BY m.id, m.commercial_partner_id, m.invoice_date,
                       m.invoice_date_due, m.amount_untaxed_signed
            )
            SELECT pid,
                   SUM((f_pago - f_factura) * GREATEST(monto, 0))
                     / NULLIF(SUM(GREATEST(monto, 0)), 0)      AS dias,
                   COUNT(*)                                     AS n,
                   COUNT(*) FILTER (WHERE f_pago <= f_vence)    AS n_a_tiempo
              FROM conciliado
          GROUP BY pid
        """, (tuple(ids), desde, hasta))
        out = {}
        for r in self.env.cr.dictfetchall():
            n = r["n"] or 0
            out[r["pid"]] = {
                "dias": float(r["dias"] or 0.0),
                "pct_a_tiempo": (r["n_a_tiempo"] / n * 100.0) if n else 0.0,
            }
        return out

    def _ag_sql_pipeline(self, ids, desde, hasta):
        self.env.cr.execute("""
            SELECT p.commercial_partner_id AS pid,
                   SUM(CASE WHEN o.state IN ('draft', 'sent')
                            THEN o.amount_untaxed ELSE 0 END)                  AS cotiz,
                   COUNT(*) FILTER (WHERE o.state IN ('draft', 'sent'))        AS cotiz_cant,
                   SUM(CASE WHEN o.state = 'sale'
                             AND o.delivery_status IN ('pending', 'started', 'partial')
                            THEN o.amount_untaxed ELSE 0 END)                  AS backlog,
                   COUNT(*) FILTER (WHERE o.state = 'sale'
                             AND o.delivery_status IN ('pending', 'started', 'partial')) AS backlog_cant,
                   MAX(CASE WHEN o.state = 'sale'
                             AND o.delivery_status IN ('pending', 'started', 'partial')
                            THEN %s - o.date_order::date ELSE 0 END)           AS backlog_dias,
                   SUM(CASE WHEN o.state = 'sale' AND o.invoice_status = 'to invoice'
                            THEN o.amount_untaxed ELSE 0 END)                  AS sin_facturar,
                   COUNT(*) FILTER (WHERE o.date_order::date >= %s)            AS emitidas,
                   COUNT(*) FILTER (WHERE o.date_order::date >= %s
                                      AND o.state = 'sale')                    AS confirmadas
              FROM sale_order o
              JOIN res_partner p ON p.id = o.partner_id
             WHERE p.commercial_partner_id IN %s
               AND o.state != 'cancel'
          GROUP BY p.commercial_partner_id
        """, (hasta, desde, desde, tuple(ids)))
        out = {}
        for r in self.env.cr.dictfetchall():
            emitidas = r["emitidas"] or 0
            out[r["pid"]] = {
                "cotiz": float(r["cotiz"] or 0.0),
                "cotiz_cant": r["cotiz_cant"] or 0,
                "backlog": float(r["backlog"] or 0.0),
                "backlog_cant": r["backlog_cant"] or 0,
                "backlog_dias": r["backlog_dias"] or 0,
                "sin_facturar": float(r["sin_facturar"] or 0.0),
                "conversion": ((r["confirmadas"] or 0) / emitidas * 100.0) if emitidas else 0.0,
            }
        return out

    def _ag_sql_entregas(self, ids, desde, hasta):
        """Entregas y lead time, separando mostrador de entrega programada."""
        self.env.cr.execute("""
            WITH ent AS (
                SELECT p.commercial_partner_id AS pid,
                       sp.id,
                       EXTRACT(EPOCH FROM (sp.date_done - o.date_order)) / 3600.0 AS horas,
                       (sp.state = 'done') AS completa
                  FROM stock_picking sp
                  JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                  JOIN res_partner p ON p.id = sp.partner_id
             LEFT JOIN sale_order o ON o.id = sp.sale_id
                 WHERE p.commercial_partner_id IN %s
                   AND spt.code = 'outgoing'
                   AND sp.state IN ('done', 'cancel')
                   AND sp.date_done >= %s AND sp.date_done <= %s
            )
            SELECT pid,
                   COUNT(*)                                                   AS entregas,
                   COUNT(*) FILTER (WHERE completa)                           AS completas,
                   COUNT(*) FILTER (WHERE horas IS NOT NULL AND horas <= %s)  AS inmediatas,
                   AVG(horas / 24.0) FILTER (WHERE horas > %s)                AS lead_prog
              FROM ent
          GROUP BY pid
        """, (tuple(ids), desde, hasta, HORAS_DESPACHO_INMEDIATO, HORAS_DESPACHO_INMEDIATO))
        out = {}
        for r in self.env.cr.dictfetchall():
            n = r["entregas"] or 0
            out[r["pid"]] = {
                "entregas": n,
                "pct_completas": ((r["completas"] or 0) / n * 100.0) if n else 0.0,
                "pct_inmediato": ((r["inmediatas"] or 0) / n * 100.0) if n else 0.0,
                "lead_prog": float(r["lead_prog"] or 0.0),
            }
        return out

    def _ag_sql_skus(self, ids, desde, hasta):
        self.env.cr.execute("""
            SELECT m.commercial_partner_id AS pid,
                   COUNT(DISTINCT aml.product_id) AS skus
              FROM account_move m
              JOIN account_move_line aml ON aml.move_id = m.id
             WHERE m.commercial_partner_id IN %s
               AND m.move_type = 'out_invoice'
               AND m.state = 'posted'
               AND m.invoice_date >= %s AND m.invoice_date <= %s
               AND aml.product_id IS NOT NULL
          GROUP BY m.commercial_partner_id
        """, (tuple(ids), desde, hasta))
        return {r["pid"]: r["skus"] for r in self.env.cr.dictfetchall()}

    # ------------------------------------------------------------------
    # Estadística
    # ------------------------------------------------------------------
    @staticmethod
    def _ag_bloques(serie, n):
        reciente = sum(serie[-n:]) if len(serie) >= n else sum(serie)
        anterior = sum(serie[-2 * n:-n]) if len(serie) >= 2 * n else 0.0
        return reciente, anterior

    def _ag_tendencia(self, serie, n):
        """Devuelve (velocidad %, dirección %).

        Velocidad compara el bloque reciente contra el anterior — reacciona
        rápido. Dirección es la pendiente de la regresión sobre la ventana
        completa, normalizada contra el promedio mensual — dice hacia dónde va
        la relación entera. Cuando ambas discrepan, el caso merece mirada
        humana, y por eso existe el estado 'divergente'.
        """
        reciente, anterior = self._ag_bloques(serie, n)
        velocidad = ((reciente - anterior) / anterior * 100.0) if anterior else (
            100.0 if reciente else 0.0)

        k = len(serie)
        if k < 3:
            return velocidad, 0.0
        x_prom = (k - 1) / 2.0
        y_prom = sum(serie) / k
        num = sum((i - x_prom) * (serie[i] - y_prom) for i in range(k))
        den = sum((i - x_prom) ** 2 for i in range(k))
        pendiente = (num / den) if den else 0.0
        direccion = (pendiente / y_prom * 100.0) if y_prom else 0.0
        return velocidad, direccion

    @staticmethod
    def _ag_coef_variacion(serie):
        if not serie:
            return 0.0
        prom = sum(serie) / len(serie)
        if not prom:
            return 0.0
        var = sum((x - prom) ** 2 for x in serie) / len(serie)
        return (var ** 0.5) / prom

    def _ag_serie_etiquetada(self, serie, hasta):
        meses_es = ["ene", "feb", "mar", "abr", "may", "jun",
                    "jul", "ago", "sep", "oct", "nov", "dic"]
        ancla = date(hasta.year, hasta.month, 1)
        out = []
        for i, valor in enumerate(reversed(serie)):
            m = ancla - relativedelta(months=i)
            out.append({"mes": meses_es[m.month - 1], "anio": m.year, "valor": round(valor, 2)})
        return list(reversed(out))

    def _ag_dias_pactados(self, partner):
        """Días de crédito pactados, de la condición de venta o del término."""
        texto = (partner.ag_condiciones_de_venta or "").upper()
        if "CONTADO" in texto or "EFECTIVO" in texto:
            return 0
        term = partner.property_payment_term_id
        if term and term.line_ids:
            return max(term.line_ids.mapped("nb_days") or [0])
        digitos = "".join(ch for ch in texto if ch.isdigit())
        return int(digitos) if digitos else 30

    # ------------------------------------------------------------------
    # Score
    # ------------------------------------------------------------------
    @staticmethod
    def _ag_percentil(valor, poblacion, invertir=False):
        """Posición 0-100 del valor dentro de su población.

        Percentil y no min-max: un solo cliente enorme aplastaría la escala de
        todos los demás si se normalizara contra el máximo.
        """
        if not poblacion:
            return 50.0
        if len(poblacion) == 1:
            return 50.0
        menores = sum(1 for x in poblacion if x < valor)
        iguales = sum(1 for x in poblacion if x == valor)
        p = (menores + iguales / 2.0) / len(poblacion) * 100.0
        return 100.0 - p if invertir else p

    def _ag_puntuar(self, cfg, bruto):
        """Normaliza por percentil dentro del segmento y arma el score."""
        grupos = {}
        for p in self:
            if not p.ag_desemp_evaluable:
                continue
            grupos.setdefault(p.ag_segmento_categoria or "__sin_segmento__", []).append(p)

        for segmento, miembros in grupos.items():
            # Un segmento de uno o dos no es población: se compara contra la
            # cartera completa antes que producir un percentil sin sentido.
            poblacion = miembros if len(miembros) >= 5 else [
                p for p in self if p.ag_desemp_evaluable]

            v_venta = [p.ag_venta_12m for p in poblacion]
            # El margen absurdamente alto casi siempre es costo mal cargado.
            # Se excluye de la población para que no desplace la escala.
            v_margen = [p.ag_margen_pct for p in poblacion
                        if p.ag_margen_pct < cfg.margen_sospechoso]
            v_desv = [p.ag_desviacion_pago for p in poblacion]
            v_venc = [p.ag_cxc_vencido_pct for p in poblacion]
            v_freq = [p.ag_meses_activos for p in poblacion]
            v_cv = [p.ag_cv_compra for p in poblacion]
            v_recencia = [p.ag_dias_sin_comprar for p in poblacion]
            v_devol = [p.ag_devoluciones_pct for p in poblacion]

            for p in miembros:
                sc_valor = self._ag_percentil(p.ag_venta_12m, v_venta)

                margen_efectivo = (p.ag_margen_pct
                                   if p.ag_margen_pct < cfg.margen_sospechoso
                                   else min(p.ag_margen_pct, cfg.margen_sospechoso))
                sc_rent = self._ag_percentil(margen_efectivo, v_margen)

                sc_pago = (
                    self._ag_percentil(p.ag_desviacion_pago, v_desv, invertir=True) * 0.45
                    + min(p.ag_pct_a_tiempo, 100.0) * 0.35
                    + self._ag_percentil(p.ag_cxc_vencido_pct, v_venc, invertir=True) * 0.20
                )

                sc_cons = (
                    self._ag_percentil(p.ag_meses_activos, v_freq) * 0.40
                    + self._ag_percentil(p.ag_cv_compra, v_cv, invertir=True) * 0.30
                    + self._ag_percentil(p.ag_dias_sin_comprar, v_recencia, invertir=True) * 0.30
                )

                sc_oper = (
                    self._ag_percentil(p.ag_devoluciones_pct, v_devol, invertir=True) * 0.50
                    + min(p.ag_pct_entregas_completas, 100.0) * 0.50
                )

                score = (
                    sc_valor * cfg.peso_valor
                    + sc_rent * cfg.peso_rentabilidad
                    + sc_pago * cfg.peso_pago
                    + sc_cons * cfg.peso_consistencia
                    + sc_oper * cfg.peso_operativo
                ) / 100.0

                p.write({
                    "ag_sc_valor": sc_valor,
                    "ag_sc_rentabilidad": sc_rent,
                    "ag_sc_pago": sc_pago,
                    "ag_sc_consistencia": sc_cons,
                    "ag_sc_operativo": sc_oper,
                    "ag_desemp_score": score,
                    "ag_desemp_grado": self._ag_grado(score, cfg),
                    "ag_desemp_tendencia": self._ag_clasificar_tendencia(p, cfg),
                })

        for p in self.filtered(lambda x: not x.ag_desemp_evaluable):
            p.write({
                "ag_desemp_score": 0.0,
                "ag_desemp_grado": "nuevo",
                "ag_desemp_tendencia": self._ag_clasificar_tendencia(p, cfg),
            })

    @staticmethod
    def _ag_grado(score, cfg):
        if score >= cfg.corte_a:
            return "a"
        if score >= cfg.corte_b:
            return "b"
        if score >= cfg.corte_c:
            return "c"
        return "d"

    @staticmethod
    def _ag_recencia(ultima, dias_sin):
        if not ultima:
            return "nunca"
        if dias_sin <= 30:
            return "r0"
        if dias_sin <= 60:
            return "r1"
        if dias_sin <= 90:
            return "r2"
        return "r3"

    def _ag_clasificar_tendencia(self, p, cfg):
        # Un cliente sin historia no se está deteriorando: nunca tuvo de qué
        # caer. Sin este corte, el que jamás compró entra por la regla de
        # dormido y sale como «deterioro grave», que es una acusación falsa
        # y contamina cualquier gráfico agrupado por tendencia.
        if not p.ag_ultima_compra:
            return "sin_historia"
        # Con un solo mes activo no hay dos puntos que comparar; velocidad y
        # dirección salen de una recta que no existe.
        if p.ag_meses_activos < 2:
            return "sin_historia"
        vel, direc = p.ag_tend_velocidad, p.ag_tend_direccion
        if p.ag_dias_sin_comprar >= cfg.dias_dormido:
            return "baja_fuerte"
        if vel <= -cfg.caida_deterioro_grave and direc < 0:
            return "baja_fuerte"
        if vel > 15 and direc > 15:
            return "subida_fuerte"
        if abs(vel) <= 8 and abs(direc) <= 8:
            return "estable"
        if vel > 0 and direc > 0:
            return "subida"
        if vel < 0 and direc < 0:
            return "baja"
        return "divergente"

    # ------------------------------------------------------------------
    # Alertas
    # ------------------------------------------------------------------
    def _ag_evaluar_alertas(self, cfg):
        Alerta = self.env["ag.desempeno.alerta"]

        def rd(x):
            return "RD$ {:,.2f}".format(x or 0.0)

        for p in self:
            vivas = Alerta.search([("partner_id", "=", p.id), ("activa", "=", True)])
            tipos_vigentes = set()

            limite = float(p.ag_limite_de_credito or p.credit_limit or 0.0)
            if limite and p.ag_credito_disponible < 0:
                tipos_vigentes.add("credito_excedido")
                Alerta.sembrar(
                    p, "credito_excedido", "crit",
                    "Crédito excedido en %s" % rd(abs(p.ag_credito_disponible)),
                    "CxC de %s más backlog de %s contra un límite de %s. "
                    "Bloquear despacho hasta regularizar."
                    % (rd(p.ag_cxc_total), rd(p.ag_backlog_monto), rd(limite)),
                    abs(p.ag_credito_disponible))
            elif limite and p.ag_uso_credito_pct >= cfg.uso_credito_alerta:
                tipos_vigentes.add("uso_credito")
                Alerta.sembrar(
                    p, "uso_credito", "warn",
                    "Uso de crédito al %.0f%%" % p.ag_uso_credito_pct,
                    "Queda %s disponible de %s." % (rd(p.ag_credito_disponible), rd(limite)),
                    p.ag_uso_credito_pct)

            # La combinada es la señal temprana más valiosa: cada mitad por
            # separado es ruido, juntas anticipan fuga o problema financiero.
            cae = p.ag_tend_velocidad <= -cfg.caida_deterioro
            paga_tarde = p.ag_desviacion_pago >= cfg.dias_pago_estirado
            if cae and paga_tarde:
                tipos_vigentes.add("deterioro_combinado")
                Alerta.sembrar(
                    p, "deterioro_combinado", "crit",
                    "Deterioro combinado — compra menos y paga más tarde",
                    "Volumen %.1f%% contra el bloque anterior y %.0f días de "
                    "atraso sobre lo pactado. Visita del gerente."
                    % (p.ag_tend_velocidad, p.ag_desviacion_pago),
                    p.ag_tend_velocidad)
            elif p.ag_tend_velocidad <= -cfg.caida_deterioro_grave:
                tipos_vigentes.add("deterioro")
                Alerta.sembrar(
                    p, "deterioro", "crit",
                    "Caída de %.1f%% en el volumen" % abs(p.ag_tend_velocidad),
                    "Bloque reciente %s contra %s del anterior."
                    % (rd(p.ag_venta_bloque), rd(p.ag_venta_bloque_prev)),
                    p.ag_tend_velocidad)
            elif p.ag_tend_velocidad <= -cfg.caida_deterioro:
                tipos_vigentes.add("deterioro")
                Alerta.sembrar(
                    p, "deterioro", "warn",
                    "Caída de %.1f%% en el volumen" % abs(p.ag_tend_velocidad),
                    "Bloque reciente %s contra %s del anterior."
                    % (rd(p.ag_venta_bloque), rd(p.ag_venta_bloque_prev)),
                    p.ag_tend_velocidad)

            if p.ag_venta_12m and p.ag_dias_sin_comprar >= cfg.dias_dormido:
                sev = "crit" if p.ag_dias_sin_comprar >= cfg.dias_dormido_critico else "warn"
                tipos_vigentes.add("dormido")
                Alerta.sembrar(
                    p, "dormido", sev,
                    "Sin comprar hace %s días" % p.ag_dias_sin_comprar,
                    "Última factura el %s. Compró %s en los últimos doce meses."
                    % (p.ag_ultima_compra and p.ag_ultima_compra.strftime("%d/%m/%Y") or "—",
                       rd(p.ag_venta_12m)),
                    p.ag_dias_sin_comprar)

            if p.ag_venta_12m and 0 < p.ag_margen_pct < cfg.margen_minimo:
                tipos_vigentes.add("margen_erosionado")
                Alerta.sembrar(
                    p, "margen_erosionado", "warn",
                    "Margen de %.1f%%, bajo el mínimo" % p.ag_margen_pct,
                    "Contribución de %s sobre ventas de %s. Revisar lista de "
                    "precio y descuentos." % (rd(p.ag_margen_12m), rd(p.ag_venta_12m)),
                    p.ag_margen_pct)

            if p.ag_margen_pct >= cfg.margen_sospechoso:
                tipos_vigentes.add("costeo_dudoso")
                Alerta.sembrar(
                    p, "costeo_dudoso", "warn",
                    "Margen de %.1f%% — revisar costeo" % p.ag_margen_pct,
                    "Un margen de este nivel casi siempre indica costo mal "
                    "cargado, no rentabilidad real. El score se calcula con el "
                    "margen limitado a %.0f%% para no premiar el error."
                    % cfg.margen_sospechoso,
                    p.ag_margen_pct)

            if p.ag_backlog_dias >= cfg.dias_backlog_estancado and p.ag_backlog_monto:
                tipos_vigentes.add("backlog_estancado")
                Alerta.sembrar(
                    p, "backlog_estancado", "warn",
                    "Backlog de %s sin despachar" % rd(p.ag_backlog_monto),
                    "%s orden(es) abiertas, la más antigua de hace %s días."
                    % (p.ag_backlog_cant, p.ag_backlog_dias),
                    p.ag_backlog_monto)

            if p.ag_sin_facturar_monto > 0:
                tipos_vigentes.add("sin_facturar")
                Alerta.sembrar(
                    p, "sin_facturar", "warn",
                    "%s entregado sin facturar" % rd(p.ag_sin_facturar_monto),
                    "Mercancía despachada que aún no genera cuenta por cobrar.",
                    p.ag_sin_facturar_monto)

            if p.ag_dias_atraso_max >= cfg.dias_vencido_critico or \
                    (p.ag_estado_de_credito or "").strip() in ("Suspendido", "Legal"):
                tipos_vigentes.add("vencido_critico")
                Alerta.sembrar(
                    p, "vencido_critico", "crit",
                    "Vencido crítico — %s días de atraso" % p.ag_dias_atraso_max,
                    "Vencido total de %s. Estado de crédito: %s."
                    % (rd(p.ag_cxc_vencido), p.ag_estado_de_credito or "sin definir"),
                    p.ag_dias_atraso_max)

            # La brecha sólo tiene sentido si alguien estimó el potencial:
            # sin ese número el módulo no puede saber cuánto falta por tomar.
            if p.ag_potencial_mes and p.ag_captura_pct < cfg.captura_minima:
                tipos_vigentes.add("brecha_potencial")
                Alerta.sembrar(
                    p, "brecha_potencial", "warn",
                    "Sólo capturamos el %.0f%% de su potencial" % p.ag_captura_pct,
                    "Compra %s al mes de un potencial estimado de %s: quedan %s "
                    "mensuales en manos de otro proveedor."
                    % (rd(p.ag_venta_mensual), rd(p.ag_potencial_mes), rd(p.ag_brecha_mes)),
                    p.ag_captura_pct)

            # Un cliente lejano con margen apretado puede estar dando pérdida
            # sin que el margen bruto lo muestre.
            if p.ag_costo_logistica and p.ag_venta_12m and \
                    p.ag_margen_neto_pct < cfg.margen_minimo:
                tipos_vigentes.add("logistica_come_margen")
                Alerta.sembrar(
                    p, "logistica_come_margen", "warn",
                    "La entrega se come el margen — queda %.1f%% neto" % p.ag_margen_neto_pct,
                    "Margen bruto %.1f%% menos %s%% de logística (%s al mes, %s km "
                    "desde planta). Revisar precio, frecuencia de entrega o pedido mínimo."
                    % (p.ag_margen_pct, p.ag_costo_logistica,
                       rd(p.ag_costo_logistica_monto), p.ag_km_planta or 0),
                    p.ag_margen_neto_pct)

            if p.ag_participacion_pct >= cfg.concentracion_alerta:
                tipos_vigentes.add("concentracion")
                Alerta.sembrar(
                    p, "concentracion", "info",
                    "Concentra el %.1f%% de la facturación" % p.ag_participacion_pct,
                    "Perder este cliente mueve la aguja de la compañía. "
                    "Tratar como cuenta estratégica.",
                    p.ag_participacion_pct)

            # Lo que dejó de cumplirse se archiva, no se borra: el historial de
            # alertas es parte de la conversación con el cliente.
            vivas.filtered(lambda a: a.tipo not in tipos_vigentes).write({"activa": False})

        self._ag_refrescar_conteos()

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------
    def _ag_guardar_snapshot(self, corte):
        Snap = self.env["ag.desempeno.snapshot"]
        inicio = date(corte.year, corte.month, 1)
        meses_es = ["ene", "feb", "mar", "abr", "may", "jun",
                    "jul", "ago", "sep", "oct", "nov", "dic"]
        periodo = "%s %s" % (meses_es[corte.month - 1], corte.year)

        self.env.cr.execute("""
            SELECT m.commercial_partner_id AS pid,
                   SUM(m.amount_untaxed_signed)              AS venta,
                   SUM(COALESCE(m.x_total_margin_amount, 0)) AS margen,
                   COUNT(*) FILTER (WHERE m.move_type = 'out_invoice') AS facturas,
                   SUM(CASE WHEN m.move_type = 'out_refund'
                            THEN -m.amount_untaxed_signed ELSE 0 END) AS devol
              FROM account_move m
             WHERE m.commercial_partner_id IN %s
               AND m.move_type IN ('out_invoice', 'out_refund')
               AND m.state = 'posted'
               AND m.invoice_date >= %s AND m.invoice_date <= %s
          GROUP BY m.commercial_partner_id
        """, (tuple(self.ids), inicio, corte))
        mes = {r["pid"]: r for r in self.env.cr.dictfetchall()}

        for p in self:
            d = mes.get(p.id, {})
            venta = float(d.get("venta") or 0.0)
            margen = float(d.get("margen") or 0.0)
            existente = Snap.search([
                ("partner_id", "=", p.id), ("fecha_corte", "=", corte)], limit=1)
            vals = {
                "partner_id": p.id,
                "fecha_corte": corte,
                "periodo": periodo,
                "venta_neta": venta,
                "margen_monto": margen,
                "margen_pct": (margen / venta * 100.0) if venta else 0.0,
                "facturas": d.get("facturas") or 0,
                "devoluciones": float(d.get("devol") or 0.0),
                "venta_12m": p.ag_venta_12m,
                "margen_12m": p.ag_margen_12m,
                "margen_pct_12m": p.ag_margen_pct,
                "score": p.ag_desemp_score,
                "grado": p.ag_desemp_grado,
                "tendencia": p.ag_desemp_tendencia,
                "cxc_total": p.ag_cxc_total,
                "cxc_vencido": p.ag_cxc_vencido,
                "dias_cobro": p.ag_dias_cobro_real,
                "segmento": p.ag_segmento_categoria,
                "user_id": p.user_id.id or False,
            }
            if existente:
                existente.write(vals)
            else:
                Snap.create(vals)
        return True

    # ------------------------------------------------------------------
    # Datos para reportes y gráficos
    # ------------------------------------------------------------------
    def ag_serie(self):
        """Serie de 12 meses lista para dibujar."""
        self.ensure_one()
        try:
            return json.loads(self.ag_serie_json or "[]")
        except (ValueError, TypeError):
            return []

    def ag_facturas_abiertas(self):
        """Facturas pendientes de la matriz, ordenadas por vencimiento."""
        self.ensure_one()
        return self.env["account.move"].search([
            ("commercial_partner_id", "=", self.id),
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("payment_state", "in", ["not_paid", "partial"]),
            ("amount_residual", ">", 0),
        ], order="invoice_date_due asc, invoice_date asc")

    def ag_promedio_segmento(self, campo):
        """Promedio del campo en el segmento, para comparar en la ficha."""
        self.ensure_one()
        if not self.ag_segmento_categoria:
            return 0.0
        pares = self.search([
            ("ag_segmento_categoria", "=", self.ag_segmento_categoria),
            ("ag_desemp_evaluable", "=", True),
            ("id", "!=", self.id),
        ])
        vals = [p[campo] for p in pares if p[campo]]
        return (sum(vals) / len(vals)) if vals else 0.0

    # ------------------------------------------------------------------
    # Enlace público del estado de cuenta
    # ------------------------------------------------------------------
    def ag_generar_token(self, forzar=False):
        """Token de acceso al estado de cuenta, con caducidad.

        Un enlace de estado de cuenta se reenvía por WhatsApp y queda
        circulando: caduca, y sólo muestra al cliente que lo generó.
        """
        self.ensure_one()
        cfg = self.env["ag.desempeno.config"].get_config()
        hoy = fields.Date.context_today(self)
        vigente = (self.ag_portal_token and self.ag_portal_expira
                   and self.ag_portal_expira >= hoy)
        if vigente and not forzar:
            return self.ag_portal_token
        import secrets
        token = secrets.token_urlsafe(16)
        self.sudo().write({
            "ag_portal_token": token,
            "ag_portal_expira": hoy + timedelta(days=cfg.dias_validez_enlace),
        })
        return token

    def ag_url_estado_cuenta(self):
        self.ensure_one()
        cfg = self.env["ag.desempeno.config"].get_config()
        base = (cfg.base_url_publica
                or self.env["ir.config_parameter"].sudo().get_param("web.base.url"))
        return "%s/ag/estado-cuenta/%s/%s" % (
            base.rstrip("/"), self.id, self.ag_generar_token())

    # ------------------------------------------------------------------
    # Acciones de la ficha
    # ------------------------------------------------------------------
    def action_ver_alertas(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Alertas — %s") % self.display_name,
            "res_model": "ag.desempeno.alerta",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def action_ver_historial(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Historial — %s") % self.display_name,
            "res_model": "ag.desempeno.snapshot",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
        }

    def action_enviar_reporte(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Enviar reporte"),
            "res_model": "ag.envio.reporte",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_ids": [(6, 0, self.ids)]},
        }
