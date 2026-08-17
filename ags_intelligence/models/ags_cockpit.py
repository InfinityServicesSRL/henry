# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _

# Indicadores del bloque ejecutivo, en el orden en que se muestran.
# Diez es el limite deliberado: con 54 calculadores disponibles, un cockpit
# que muestre todos no se lee. La seleccion cubre los cuatro ejes que
# deciden la salud del negocio -- margen, liquidez, cartera y riesgo -- mas
# los dos indicadores de disciplina que mas cuestan dinero.
EJECUTIVO = [
    ("MARGEN_BRUTO",        "Margen bruto",          "margen"),
    ("MARGEN_EBITDA",       "EBITDA",                "margen"),
    ("RAZON_CORRIENTE",     "Razon corriente",       "liquidez"),
    ("DEUDA_EBITDA",        "Deuda / EBITDA",        "liquidez"),
    ("DSO",                 "Dias de cobro",         "cartera"),
    ("ATRASO_CARTERA_VIVA", "Atraso de cartera",     "cartera"),
    ("PCT_CARTERA_CORRIENTE", "Cartera corriente",   "cartera"),
    ("EXPOSICION_USD",      "Exposicion USD",        "riesgo"),
    ("PCT_DEVOLUCIONES",    "Devoluciones",          "disciplina"),
    ("AJUSTES_INVENTARIO",  "Ajustes de inventario", "disciplina"),
]

EJES = {
    "margen": "Margen",
    "liquidez": "Liquidez y solvencia",
    "cartera": "Cartera",
    "riesgo": "Riesgo",
    "disciplina": "Disciplina operativa",
}


class AgsCockpit(models.AbstractModel):
    """Datos del cockpit de gerencia.

    ESTRUCTURA EN DOS ZONAS, y el orden importa:

      1. EXCEPCIONES: solo lo que requiere atencion. Si no hay nada, la zona
         se colapsa y lo dice. Eso convierte la pantalla vacia en informacion
         util en lugar de en un espacio desaprovechado.

      2. EJECUTIVO: diez indicadores fijos con tendencia contra el mes
         anterior. Da panorama estable, y al ser siempre los mismos permite
         reconocer un cambio de un vistazo.

    Las excepciones van primero porque el cockpit se abre para saber si hay
    algo que atender, no para admirar el panorama.
    """
    _name = "ags.cockpit"
    _description = "AG Intelligence - Cockpit de Gerencia"

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    @api.model
    def _cierre_mes(self, fecha=None, atras=0):
        f = fecha or fields.Date.context_today(self)
        primero = f.replace(day=1) - relativedelta(months=atras)
        return primero + relativedelta(months=1, days=-1)

    @api.model
    def _formato(self, valor, unidad):
        """Formatea segun la unidad, con separador de miles dominicano."""
        if unidad == "pct":
            return "%.2f%%" % valor
        if unidad == "dias":
            return "%.0f d" % valor
        if unidad in ("dop", "usd"):
            simbolo = "RD$" if unidad == "dop" else "US$"
            return "%s %s" % (simbolo, "{:,.0f}".format(valor))
        if unidad == "ratio":
            return "%.2f" % valor
        if unidad == "cantidad":
            return "{:,.0f}".format(valor)
        if unidad == "kwh_ton":
            return "%.1f kWh/t" % valor
        return "{:,.2f}".format(valor)

    # ------------------------------------------------------------------
    # Zona 1: excepciones
    # ------------------------------------------------------------------

    @api.model
    def _excepciones(self, cierre):
        """Reune todo lo que requiere atencion, ordenado por gravedad."""
        salida = []
        Param = self.env["ags.parametro"]
        Med = self.env["ags.medicion"]

        # Indicadores en rojo contra el benchmark
        rojos = Med.search([
            ("fecha_periodo", "=", cierre),
            ("semaforo", "=", "rojo"),
        ])
        for m in rojos:
            p = m.parametro_id
            bm = p.benchmark_vigente_id
            salida.append({
                "tipo": "indicador",
                "gravedad": "danger",
                "titulo": p.name,
                "detalle": "%s frente a un objetivo de %s" % (
                    self._formato(m.valor, p.unidad),
                    self._formato(bm.valor_objetivo, p.unidad) if bm else "-"),
                "accion": "parametro",
                "res_id": p.id,
            })

        # Metas incumplidas del periodo
        metas = self.env["ags.meta"].search([
            ("fecha_cierre", "=", cierre),
            ("semaforo", "=", "rojo"),
            ("estado", "in", ["aprobada", "cerrada"]),
        ])
        for t in metas:
            quien = ""
            if t.dimension == "vendedor" and t.vendedor_id:
                quien = " · %s" % t.vendedor_id.name
            elif t.dimension == "mercado" and t.mercado_id:
                quien = " · %s" % t.mercado_id.name
            salida.append({
                "tipo": "meta",
                "gravedad": "danger",
                "titulo": "Meta incumplida: %s%s" % (t.parametro_id.name, quien),
                "detalle": "%.0f%% de cumplimiento" % t.cumplimiento,
                "accion": "meta",
                "res_id": t.id,
            })

        # Clientes que destruyen valor
        destruyen = self.env["ags.rentabilidad"].search([
            ("fecha_periodo", "=", cierre),
            ("destruye_valor", "=", True),
        ], order="margen_economico", limit=5)
        for r in destruyen:
            salida.append({
                "tipo": "cliente",
                "gravedad": "warning",
                "titulo": "Destruye valor: %s" % r.partner_id.display_name,
                "detalle": "margen economico %s sobre ventas de %s" % (
                    self._formato(r.margen_economico, "dop"),
                    self._formato(r.ventas, "dop")),
                "accion": "rentabilidad",
                "res_id": r.id,
            })

        # Indicadores cuyo dato aun no es confiable.
        # Se muestran como aviso y no como problema: no son un resultado malo,
        # son una advertencia de que ese numero todavia no significa nada.
        no_confiables = Param.search([
            ("madurez", "=", "con_reservas"),
            ("codigo", "in", [c for c, _n, _e in EJECUTIVO]),
        ])
        for p in no_confiables:
            salida.append({
                "tipo": "madurez",
                "gravedad": "info",
                "titulo": "Dato aun no confiable: %s" % p.name,
                "detalle": p.madurez_detalle or "",
                "accion": "parametro",
                "res_id": p.id,
            })

        orden = {"danger": 0, "warning": 1, "info": 2}
        return sorted(salida, key=lambda x: orden.get(x["gravedad"], 9))

    # ------------------------------------------------------------------
    # Zona 2: bloque ejecutivo
    # ------------------------------------------------------------------

    @api.model
    def _ejecutivo(self, cierre, previo):
        Param = self.env["ags.parametro"]
        Med = self.env["ags.medicion"]
        tarjetas = []
        for codigo, etiqueta, eje in EJECUTIVO:
            p = Param.search([("codigo", "=", codigo)], limit=1)
            if not p:
                continue
            actual = Med.search([
                ("parametro_id", "=", p.id), ("fecha_periodo", "=", cierre),
            ], limit=1)
            anterior = Med.search([
                ("parametro_id", "=", p.id), ("fecha_periodo", "=", previo),
            ], limit=1)

            t = {
                "codigo": codigo,
                "etiqueta": etiqueta,
                "eje": eje,
                "eje_nombre": EJES.get(eje, eje),
                "parametro_id": p.id,
                "unidad": p.unidad,
                "madurez": p.madurez or "no_medible",
                "atipico": bool(actual.periodo_atipico) if actual else False,
                "hay_dato": bool(actual),
            }
            if not actual:
                t.update({
                    "valor": "Sin dato",
                    "semaforo": "sin_dato",
                    "tendencia": "",
                    "variacion": 0.0,
                    "objetivo": "",
                })
                tarjetas.append(t)
                continue

            bm = p.benchmark_vigente_id
            var = 0.0
            tend = "plana"
            if anterior and anterior.valor:
                var = (actual.valor - anterior.valor) / abs(anterior.valor) * 100.0
                if abs(var) < 1.0:
                    tend = "plana"
                elif p.direccion == "menor_mejor":
                    tend = "mejora" if var < 0 else "deterioro"
                elif p.direccion == "mayor_mejor":
                    tend = "mejora" if var > 0 else "deterioro"
                else:
                    tend = "sube" if var > 0 else "baja"

            t.update({
                "valor": self._formato(actual.valor, p.unidad),
                "valor_num": actual.valor,
                "semaforo": actual.semaforo or "sin_dato",
                "tendencia": tend,
                "variacion": round(var, 1),
                "objetivo": self._formato(bm.valor_objetivo, p.unidad) if bm else "",
            })
            tarjetas.append(t)
        return tarjetas

    # ------------------------------------------------------------------
    # Ventas del mes contra meta
    # ------------------------------------------------------------------

    @api.model
    def _ventas_vs_meta(self, cierre):
        desde = cierre.replace(day=1)
        facturas = self.env["account.move"].search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", cierre),
        ])
        real = sum(facturas.mapped("amount_untaxed"))

        metas = self.env["ags.meta"].search([
            ("fecha_cierre", "=", cierre),
            ("dimension", "=", "vendedor"),
            ("estado", "in", ["aprobada", "cerrada"]),
        ])
        meta_total = sum(metas.mapped("valor"))

        por_vendedor = []
        vend = {}
        for f in facturas:
            k = f.invoice_user_id
            if not k:
                continue
            vend[k] = vend.get(k, 0.0) + f.amount_untaxed
        metas_map = {m.vendedor_id.id: m.valor for m in metas if m.vendedor_id}
        for u, v in sorted(vend.items(), key=lambda x: -x[1]):
            mt = metas_map.get(u.id, 0.0)
            por_vendedor.append({
                "nombre": u.name,
                "real": self._formato(v, "dop"),
                "real_num": v,
                "meta": self._formato(mt, "dop") if mt else "",
                "meta_num": mt,
                "cumplimiento": round(v / mt * 100.0, 1) if mt else 0.0,
            })
        return {
            "real": self._formato(real, "dop"),
            "real_num": real,
            "meta": self._formato(meta_total, "dop") if meta_total else "",
            "meta_num": meta_total,
            "cumplimiento": round(real / meta_total * 100.0, 1) if meta_total else 0.0,
            "hay_meta": bool(meta_total),
            "por_vendedor": por_vendedor[:8],
        }

    # ------------------------------------------------------------------
    # Punto de entrada
    # ------------------------------------------------------------------

    @api.model
    def datos(self, fecha=None):
        """Devuelve todo lo que el cockpit necesita en una sola llamada."""
        if isinstance(fecha, str) and fecha:
            fecha = fields.Date.to_date(fecha)
        cierre = self._cierre_mes(fecha)
        previo = self._cierre_mes(fecha, atras=1)

        excepciones = self._excepciones(cierre)
        return {
            "periodo": cierre.strftime("%B %Y").capitalize(),
            "cierre": cierre.isoformat(),
            "previo": previo.isoformat(),
            "excepciones": excepciones,
            "n_excepciones": len([e for e in excepciones
                                  if e["gravedad"] in ("danger", "warning")]),
            "ejecutivo": self._ejecutivo(cierre, previo),
            "ventas": self._ventas_vs_meta(cierre),
            "ejes": EJES,
        }

    @api.model
    def recalcular(self, fecha=None):
        """Recalcula el periodo desde el cockpit, sin salir de la pantalla."""
        if isinstance(fecha, str) and fecha:
            fecha = fields.Date.to_date(fecha)
        f = fecha or fields.Date.context_today(self)
        self.env["ags.calculador"].calcular_periodo(f)
        self.env["ags.rentabilidad"].calcular_periodo(f)
        return self.datos(fecha)
