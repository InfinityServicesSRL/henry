# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _

# Version del contrato de datos que consume el cliente OWL.
# Si cambia la forma del payload, sube el numero y el front puede advertir en
# lugar de romperse en silencio contra una estructura que ya no existe.
CONTRATO = 1

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

# Orden de los bloques comparativos. La seccion salud_erp queda fuera a
# proposito: no es un eje de gestion, es la materia prima de la banda de
# confianza, y mostrarla dos veces diluye la advertencia.
ORDEN_BLOQUES = [
    "costos",
    "financiero",
    "demanda",
    "inventario",
    "comercial",
    "rrhh",
    "macro",
    "cockpit",
]


class AgsCockpit(models.AbstractModel):
    """Datos del cockpit de gerencia.

    ESTRUCTURA EN CUATRO ZONAS, y el orden importa:

      0. BANDA DE CONFIANZA: que tan creible es lo que sigue. Va primero
         porque un numero calculado sobre asientos en borrador e inventario
         negativo no es un numero, es una opinion mal informada.

      1. EXCEPCIONES: solo lo que requiere atencion. Si no hay nada, la zona
         se colapsa y lo dice. Eso convierte la pantalla vacia en informacion
         util en lugar de en un espacio desaprovechado.

      2. EJECUTIVO: diez indicadores fijos con tendencia. Da panorama estable,
         y al ser siempre los mismos permite reconocer un cambio de un vistazo.

      3. BLOQUES COMPARATIVOS: por eje, con las cinco columnas del diseno
         (actual, baseline, objetivo, delta contra cada uno). Responden las dos
         preguntas que importan: mejore respecto a donde arranque, y donde
         estoy frente al mercado.

    NINGUN umbral vive en este archivo. Todo semaforo sale de
    ags.benchmark.evaluar_valor() respetando ags.parametro.direccion. Cambiar
    un umbral es editar un registro, no tocar codigo ni redesplegar.
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

    @api.model
    def _sentido(self, variacion, direccion, umbral=1.0):
        """Traduce una variacion porcentual a mejora / deterioro / plana.

        Un mismo -8% es excelente en dias de cobro y pesimo en margen. La
        direccion del parametro es la unica fuente de verdad para eso.
        """
        if abs(variacion) < umbral:
            return "plana"
        if direccion == "menor_mejor":
            return "mejora" if variacion < 0 else "deterioro"
        if direccion == "mayor_mejor":
            return "mejora" if variacion > 0 else "deterioro"
        return "sube" if variacion > 0 else "baja"

    @api.model
    def _delta(self, actual, referencia, direccion, unidad):
        """Compara un valor contra una referencia.

        Devuelve siempre la misma estructura, con hay_dato en False cuando la
        referencia no existe. El front no deberia tener que distinguir entre
        'cero' y 'no hay contra que comparar': son cosas distintas y confundirlas
        es lo que hace que un tablero muestre 0.0% donde no hay informacion.
        """
        if not referencia:
            return {
                "hay_dato": False,
                "pct": 0.0,
                "abs": 0.0,
                "texto": "",
                "sentido": "sin_dato",
            }
        diferencia = actual - referencia
        pct = diferencia / abs(referencia) * 100.0
        return {
            "hay_dato": True,
            "pct": round(pct, 1),
            "abs": round(diferencia, 2),
            "texto": "%+.1f%%" % pct,
            "texto_abs": "%s%s" % ("+" if diferencia >= 0 else "-",
                                   self._formato(abs(diferencia), unidad)),
            "sentido": self._sentido(pct, direccion),
        }

    # ------------------------------------------------------------------
    # Zona 0: banda de confianza
    # ------------------------------------------------------------------

    @api.model
    def _hallazgo(self, clave, codigo_param, etiqueta, cantidad, modelo,
                  dominio, gravedad_base="warning"):
        """Arma un hallazgo de la banda y decide su gravedad.

        Si existe un parametro de Salud del ERP con benchmark cargado, manda
        el benchmark (D1). Si no lo hay, la regla minima es binaria: o esta
        limpio o no lo esta. Deliberadamente conservadora, porque el proposito
        de la banda es advertir, no tranquilizar.
        """
        gravedad = "ok" if not cantidad else gravedad_base
        semaforo = "sin_dato"
        if codigo_param:
            param = self.env["ags.parametro"].search(
                [("codigo", "=", codigo_param)], limit=1)
            if param:
                semaforo = param.evaluar(cantidad)
                if semaforo == "rojo":
                    gravedad = "danger"
                elif semaforo == "amarillo":
                    gravedad = "warning"
                elif semaforo == "verde":
                    gravedad = "ok"
        return {
            "clave": clave,
            "etiqueta": etiqueta,
            "cantidad": cantidad,
            "gravedad": gravedad,
            "semaforo": semaforo,
            "modelo": modelo,
            "dominio": dominio,
        }

    @api.model
    def _banda_confianza(self, cierre):
        """Que tan creible es el resto de la pantalla.

        Se mide contra el estado ACTUAL del ERP, no contra la medicion
        guardada del periodo: si hoy hay 40 asientos en borrador, el margen
        del mes pasado tampoco es confiable, porque esos asientos pueden
        pertenecerle.

        El tablero de terceros muestra un DSO de 187 dias y 100 lineas de
        inventario negativo sin advertir absolutamente nada. Esta zona existe
        para que eso no se repita.
        """
        hoy = fields.Date.context_today(self)
        limite_oc = hoy - relativedelta(days=15)

        dom_asientos = [("state", "=", "draft"), ("move_type", "!=", "entry")]
        dom_ots = [("state", "not in", ["done", "cancel"]),
                   ("date_finished", "<", hoy)]
        dom_pickings = [("state", "not in", ["done", "cancel"]),
                        ("scheduled_date", "<", hoy)]
        dom_oc = [("state", "in", ["draft", "sent"]),
                  ("date_order", "<=", limite_oc)]
        dom_quants = [("quantity", "<", 0),
                      ("location_id.usage", "=", "internal")]

        hallazgos = [
            self._hallazgo(
                "asientos_borrador", "ASIENTOS_BORRADOR",
                "Facturas y asientos en borrador",
                self.env["account.move"].search_count(dom_asientos),
                "account.move", dom_asientos, "danger"),
            self._hallazgo(
                "ots_vencidas", "OTS_ABIERTAS_VENCIDAS",
                "Ordenes de produccion abiertas y vencidas",
                self.env["mrp.production"].search_count(dom_ots),
                "mrp.production", dom_ots),
            self._hallazgo(
                "mov_sin_validar", "MOV_SIN_VALIDAR",
                "Movimientos de inventario sin validar",
                self.env["stock.picking"].search_count(dom_pickings),
                "stock.picking", dom_pickings),
            self._hallazgo(
                "oc_sin_confirmar", "OC_SIN_CONFIRMAR",
                "Ordenes de compra sin confirmar (mas de 15 dias)",
                self.env["purchase.order"].search_count(dom_oc),
                "purchase.order", dom_oc),
            # Sin parametro asociado: el inventario negativo no es un indicador
            # de gestion con banda tolerable, es un error de datos. Cualquier
            # cantidad distinta de cero invalida costo y margen.
            self._hallazgo(
                "inventario_negativo", None,
                "Lineas de inventario en negativo",
                self.env["stock.quant"].search_count(dom_quants),
                "stock.quant", dom_quants, "danger"),
        ]

        sucios = [h for h in hallazgos if h["cantidad"]]
        if any(h["gravedad"] == "danger" for h in sucios):
            nivel = "alerta"
        elif sucios:
            nivel = "aviso"
        else:
            nivel = "ok"

        if nivel == "ok":
            titular = _("Datos verificados: sin pendientes que distorsionen las cifras.")
        else:
            partes = ["%s %s" % (h["cantidad"], h["etiqueta"].lower()) for h in sucios[:3]]
            titular = _("Lea con reserva: %s.") % ", ".join(partes)

        return {
            "nivel": nivel,
            "titular": titular,
            "n_sucios": len(sucios),
            "hallazgos": hallazgos,
        }

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
            salida.append({
                "tipo": "indicador",
                "gravedad": "danger",
                "titulo": p.name,
                "detalle": "%s frente a un objetivo de %s" % (
                    self._formato(m.valor, p.unidad),
                    self._formato(m.valor_objetivo, p.unidad)
                    if m.valor_objetivo else "-"),
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
    def _ejecutivo(self, cierre, previo, homologo):
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
            homologa = Med.search([
                ("parametro_id", "=", p.id), ("fecha_periodo", "=", homologo),
            ], limit=1)

            t = {
                "codigo": codigo,
                "etiqueta": etiqueta,
                "eje": eje,
                "eje_nombre": EJES.get(eje, eje),
                "parametro_id": p.id,
                "unidad": p.unidad,
                "direccion": p.direccion,
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
                    "baseline": "",
                    "delta_mes": self._delta(0, 0, p.direccion, p.unidad),
                    "delta_homologo": self._delta(0, 0, p.direccion, p.unidad),
                    "delta_baseline": self._delta(0, 0, p.direccion, p.unidad),
                })
                tarjetas.append(t)
                continue

            delta_mes = self._delta(
                actual.valor, anterior.valor if anterior else 0.0,
                p.direccion, p.unidad)
            delta_homologo = self._delta(
                actual.valor, homologa.valor if homologa else 0.0,
                p.direccion, p.unidad)
            delta_baseline = self._delta(
                actual.valor, actual.valor_baseline, p.direccion, p.unidad)

            t.update({
                "valor": self._formato(actual.valor, p.unidad),
                "valor_num": actual.valor,
                "semaforo": actual.semaforo or "sin_dato",
                # tendencia y variacion se conservan por compatibilidad con la
                # vista actual; el front nuevo debe leer delta_mes.
                "tendencia": delta_mes["sentido"],
                "variacion": delta_mes["pct"],
                "objetivo": self._formato(actual.valor_objetivo, p.unidad)
                            if actual.valor_objetivo else "",
                "objetivo_num": actual.valor_objetivo,
                "baseline": self._formato(actual.valor_baseline, p.unidad)
                            if actual.valor_baseline else "",
                "baseline_num": actual.valor_baseline,
                "delta_mes": delta_mes,
                "delta_homologo": delta_homologo,
                "delta_baseline": delta_baseline,
            })
            tarjetas.append(t)
        return tarjetas

    # ------------------------------------------------------------------
    # Zona 3: bloques comparativos por eje
    # ------------------------------------------------------------------

    @api.model
    def _bloques(self, cierre, homologo):
        """Las cinco columnas del diseno, agrupadas por seccion.

        Actual · Baseline · Objetivo · Delta vs baseline · Delta vs objetivo.

        baseline y objetivo se leen de los campos almacenados de la medicion,
        no se resuelven aqui: quedaron congelados en el momento del calculo y
        recalcularlos en cada lectura abriria la puerta a que la pantalla
        muestre una comparacion distinta a la que se auditó.
        """
        mediciones = self.env["ags.medicion"].search([
            ("fecha_periodo", "=", cierre),
        ])
        homologas = {
            m.parametro_id.id: m.valor
            for m in self.env["ags.medicion"].search([
                ("fecha_periodo", "=", homologo),
            ])
        }
        etiquetas = dict(
            self.env["ags.parametro"]._fields["seccion"].selection)

        por_seccion = {}
        for m in mediciones:
            p = m.parametro_id
            if not p or p.seccion == "salud_erp":
                continue
            fila = {
                "codigo": p.codigo,
                "nombre": p.name,
                "parametro_id": p.id,
                "unidad": p.unidad,
                "direccion": p.direccion,
                "madurez": p.madurez or "no_medible",
                "atipico": m.periodo_atipico,
                "semaforo": m.semaforo or "sin_dato",
                "actual": self._formato(m.valor, p.unidad),
                "actual_num": m.valor,
                "baseline": self._formato(m.valor_baseline, p.unidad)
                            if m.valor_baseline else "",
                "baseline_num": m.valor_baseline,
                "objetivo": self._formato(m.valor_objetivo, p.unidad)
                            if m.valor_objetivo else "",
                "objetivo_num": m.valor_objetivo,
                "delta_baseline": self._delta(
                    m.valor, m.valor_baseline, p.direccion, p.unidad),
                "delta_objetivo": self._delta(
                    m.valor, m.valor_objetivo, p.direccion, p.unidad),
                "delta_homologo": self._delta(
                    m.valor, homologas.get(p.id, 0.0), p.direccion, p.unidad),
                "secuencia": p.secuencia,
            }
            por_seccion.setdefault(p.seccion, []).append(fila)

        bloques = []
        secciones = ORDEN_BLOQUES + [s for s in por_seccion if s not in ORDEN_BLOQUES]
        for seccion in secciones:
            filas = por_seccion.get(seccion)
            if not filas:
                continue
            filas.sort(key=lambda f: (f["secuencia"], f["nombre"]))
            bloques.append({
                "eje": seccion,
                "nombre": etiquetas.get(seccion, seccion),
                "n_rojos": len([f for f in filas if f["semaforo"] == "rojo"]),
                "filas": filas,
            })
        return bloques

    # ------------------------------------------------------------------
    # Alertas del periodo
    # ------------------------------------------------------------------

    @api.model
    def _alertas(self, cierre, limite=15):
        """Alertas abiertas del periodo, listas para el drill-down."""
        registros = self.env["ags.alerta"].search([
            ("fecha_periodo", "=", cierre),
            ("estado", "=", "abierta"),
        ], limit=limite)
        prioridades = dict(self.env["ags.alerta"]._fields["prioridad"].selection)
        tipos = dict(self.env["ags.alerta"]._fields["tipo"].selection)
        salida = []
        for a in registros:
            salida.append({
                "id": a.id,
                "titulo": a.titulo,
                "detalle": a.detalle or "",
                "recomendacion": a.recomendacion or "",
                "tipo": a.tipo,
                "tipo_nombre": tipos.get(a.tipo, a.tipo),
                "prioridad": a.prioridad,
                "prioridad_nombre": prioridades.get(a.prioridad, a.prioridad),
                "periodos_seguidos": a.periodos_seguidos,
                "parametro_id": a.parametro_id.id or False,
                "responsable": a.responsable_id.name or "",
            })
        return salida

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
        """Devuelve todo lo que el cockpit necesita en una sola llamada.

        Un unico viaje al servidor por dos razones: el front no puede quedar
        mostrando una zona con datos de un periodo y otra con datos de otro,
        y cada zona extra que se resuelva aparte multiplica los estados de
        carga que hay que manejar.

        El periodo homologo es el mismo mes del año anterior, nunca el
        acumulado del año completo. Comparar ocho meses contra doce y reportar
        la diferencia como si fuera real es el error mas caro que comete el
        tablero de referencia.
        """
        if isinstance(fecha, str) and fecha:
            fecha = fields.Date.to_date(fecha)
        cierre = self._cierre_mes(fecha)
        previo = self._cierre_mes(fecha, atras=1)
        homologo = self._cierre_mes(fecha, atras=12)

        excepciones = self._excepciones(cierre)
        return {
            "contrato": CONTRATO,
            "periodo": cierre.strftime("%B %Y").capitalize(),
            "cierre": cierre.isoformat(),
            "previo": previo.isoformat(),
            "homologo": homologo.isoformat(),
            "confianza": self._banda_confianza(cierre),
            "excepciones": excepciones,
            "n_excepciones": len([e for e in excepciones
                                  if e["gravedad"] in ("danger", "warning")]),
            "ejecutivo": self._ejecutivo(cierre, previo, homologo),
            "bloques": self._bloques(cierre, homologo),
            "alertas": self._alertas(cierre),
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
