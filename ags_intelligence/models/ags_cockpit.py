# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _
from odoo.exceptions import AccessError
from odoo.tools.misc import format_date

# Version del contrato de datos que consume el cliente OWL.
# Si cambia la forma del payload, sube el numero y el front puede advertir en
# lugar de romperse en silencio contra una estructura que ya no existe.
CONTRATO = 2

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

# Filtros que el cockpit acepta. Cada uno declara hasta donde llega.
FILTROS = ("vendedor_id", "mercado_id", "almacen_id")


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

    ALCANCE DE LOS FILTROS. ags.medicion guarda valores agregados de toda la
    empresa: no tiene dimension de vendedor, mercado ni almacen. Por eso un
    filtro solo puede afectar de verdad a las ventas, las metas, la
    rentabilidad por cliente y la parte de la banda que vive en almacenes.
    Cada zona declara su alcance en el payload y el front sella las que no
    responden al filtro. Un filtro que se aplica a medias y no lo dice hace
    mas dano que no tener filtro: el gerente cree estar viendo a un vendedor
    y esta viendo a la empresa entera.

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
    def _es_gerencia(self):
        """Quien puede ver la informacion sensible.

        Las reglas de registro ya filtran parametros y mediciones
        confidenciales por si solas, asi que las tarjetas y los bloques se
        depuran sin que este metodo intervenga. Hace falta igualmente para
        dos cosas que las reglas no cubren: los modelos que el delegado no
        puede ni leer (consultarlos lanzaria AccessError en vez de devolver
        una lista vacia) y el desglose de ventas de otros vendedores.
        """
        return self.env.user.has_group("ags_intelligence.group_ags_manager")

    @api.model
    def _contar(self, modelo, dominio):
        """Cuenta registros tolerando que el usuario no tenga acceso.

        Un delegado sin permisos de contabilidad no puede contar asientos en
        borrador. La banda de confianza omite ese hallazgo en lugar de
        reventar la pantalla entera, y lo omite en silencio: decirle a quien
        no puede verlo que existe algo que no puede ver no le sirve de nada.
        """
        try:
            return self.env[modelo].search_count(dominio)
        except AccessError:
            return None

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
    # Filtros
    # ------------------------------------------------------------------

    @api.model
    def _normalizar(self, filtros):
        """Limpia lo que llega del cliente y descarta lo que no exista.

        Un id que ya no existe se ignora en lugar de romper: el usuario pudo
        dejar la pantalla abierta mientras alguien archivaba un almacen.
        """
        salida = {}
        for clave in FILTROS:
            valor = (filtros or {}).get(clave)
            if not valor:
                continue
            try:
                salida[clave] = int(valor)
            except (TypeError, ValueError):
                continue
        modelos = {
            "vendedor_id": "res.users",
            "mercado_id": "ags.mercado",
            "almacen_id": "stock.warehouse",
        }
        for clave, modelo in modelos.items():
            if clave in salida:
                registro = self.env[modelo].browse(salida[clave]).exists()
                if not registro:
                    salida.pop(clave)
        return salida

    @api.model
    def opciones_filtros(self):
        """Valores para los selectores, acotados a lo que tiene sentido elegir.

        Los vendedores se sacan de quien realmente facturo en los ultimos doce
        meses, no de la lista completa de usuarios: un desplegable con sesenta
        nombres de los que cinco venden no es un filtro, es un obstaculo.
        """
        desde = self._cierre_mes(atras=12).replace(day=1)
        grupos = self.env["account.move"]._read_group(
            [
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("invoice_date", ">=", desde),
                ("invoice_user_id", "!=", False),
            ],
            groupby=["invoice_user_id"],
        )
        vendedores = [
            {"id": g[0].id, "nombre": g[0].name}
            for g in grupos if g[0]
        ]
        vendedores.sort(key=lambda v: v["nombre"])
        return {
            "vendedores": vendedores,
            "mercados": [
                {"id": m.id, "nombre": m.name}
                for m in self.env["ags.mercado"].search([], order="secuencia, name")
            ],
            "almacenes": [
                {"id": w.id, "nombre": w.name}
                for w in self.env["stock.warehouse"].search([], order="name")
            ],
        }

    @api.model
    def _etiquetas_filtros(self, filtros):
        """Como se leen los filtros activos en pantalla."""
        etiquetas = []
        if filtros.get("vendedor_id"):
            etiquetas.append({
                "clave": "vendedor_id",
                "campo": _("Vendedor"),
                "valor": self.env["res.users"].browse(filtros["vendedor_id"]).name,
            })
        if filtros.get("mercado_id"):
            etiquetas.append({
                "clave": "mercado_id",
                "campo": _("Mercado"),
                "valor": self.env["ags.mercado"].browse(filtros["mercado_id"]).name,
            })
        if filtros.get("almacen_id"):
            etiquetas.append({
                "clave": "almacen_id",
                "campo": _("Almacen"),
                "valor": self.env["stock.warehouse"].browse(filtros["almacen_id"]).name,
            })
        return etiquetas

    @api.model
    def _dominio_facturas(self, filtros):
        """Parte dimensional del dominio de facturas de venta."""
        dominio = []
        if filtros.get("vendedor_id"):
            dominio.append(("invoice_user_id", "=", filtros["vendedor_id"]))
        if filtros.get("mercado_id"):
            dominio.append(("partner_id.ags_mercado_id", "=", filtros["mercado_id"]))
        return dominio

    # ------------------------------------------------------------------
    # Zona 0: banda de confianza
    # ------------------------------------------------------------------

    @api.model
    def _hallazgo(self, clave, codigo_param, etiqueta, cantidad, modelo,
                  dominio, gravedad_base="warning", es_global=False):
        """Arma un hallazgo de la banda y decide su gravedad.

        Si existe un parametro de Salud del ERP con benchmark cargado, manda
        el benchmark (D1). Si no lo hay, la regla minima es binaria: o esta
        limpio o no lo esta. Deliberadamente conservadora, porque el proposito
        de la banda es advertir, no tranquilizar.

        es_global marca los hallazgos que no responden al filtro de almacen,
        porque no viven en un almacen: un asiento en borrador es de la empresa.
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
            "es_global": es_global,
        }

    @api.model
    def _banda_confianza(self, cierre, filtros):
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

        almacen = False
        if filtros.get("almacen_id"):
            almacen = self.env["stock.warehouse"].browse(filtros["almacen_id"])
        por_almacen = (
            [("picking_type_id.warehouse_id", "=", almacen.id)] if almacen else []
        )

        dom_asientos = [("state", "=", "draft"), ("move_type", "!=", "entry")]
        dom_ots = [("state", "not in", ["done", "cancel"]),
                   ("date_finished", "<", hoy)] + por_almacen
        dom_pickings = [("state", "not in", ["done", "cancel"]),
                        ("scheduled_date", "<", hoy)] + por_almacen
        dom_oc = [("state", "in", ["draft", "sent"]),
                  ("date_order", "<=", limite_oc)]
        dom_quants = [("quantity", "<", 0)]
        if almacen and almacen.view_location_id:
            dom_quants += [("location_id", "child_of", almacen.view_location_id.id)]
        else:
            dom_quants += [("location_id.usage", "=", "internal")]

        hallazgos = [
            self._hallazgo(
                "asientos_borrador", "ASIENTOS_BORRADOR",
                "Facturas y asientos en borrador",
                self._contar("account.move", dom_asientos),
                "account.move", dom_asientos, "danger", es_global=True),
            self._hallazgo(
                "ots_vencidas", "OTS_ABIERTAS_VENCIDAS",
                "Ordenes de produccion abiertas y vencidas",
                self._contar("mrp.production", dom_ots),
                "mrp.production", dom_ots),
            self._hallazgo(
                "mov_sin_validar", "MOV_SIN_VALIDAR",
                "Movimientos de inventario sin validar",
                self._contar("stock.picking", dom_pickings),
                "stock.picking", dom_pickings),
            self._hallazgo(
                "oc_sin_confirmar", "OC_SIN_CONFIRMAR",
                "Ordenes de compra sin confirmar (mas de 15 dias)",
                self._contar("purchase.order", dom_oc),
                "purchase.order", dom_oc, es_global=True),
            # Sin parametro asociado: el inventario negativo no es un indicador
            # de gestion con banda tolerable, es un error de datos. Cualquier
            # cantidad distinta de cero invalida costo y margen.
            self._hallazgo(
                "inventario_negativo", None,
                "Lineas de inventario en negativo",
                self._contar("stock.quant", dom_quants),
                "stock.quant", dom_quants, "danger"),
        ]

        # Orden por gravedad y luego por volumen: el titular solo cita tres
        # hallazgos, y deben ser los tres peores, no los tres primeros de la
        # lista. Con 96 lineas de inventario negativo escondidas detras de 4
        # asientos en borrador, la advertencia pierde justo lo que importa.
        # cantidad None = el usuario no puede consultar ese modelo.
        hallazgos = [h for h in hallazgos if h["cantidad"] is not None]

        peso = {"danger": 0, "warning": 1, "ok": 2}
        sucios = sorted(
            [h for h in hallazgos if h["cantidad"]],
            key=lambda h: (peso.get(h["gravedad"], 9), -h["cantidad"]),
        )
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
            # Mismo orden que el titular: lo peor primero. Si la lista se
            # ordenara distinto que la frase que la encabeza, el ojo iria a la
            # primera cajita creyendo que es la mas grave.
            "hallazgos": sucios + [h for h in hallazgos if not h["cantidad"]],
            "alcance": "almacen" if almacen else "empresa",
        }

    # ------------------------------------------------------------------
    # Zona 1: excepciones
    # ------------------------------------------------------------------

    @api.model
    def _excepciones(self, cierre, filtros):
        """Reune todo lo que requiere atencion, ordenado por gravedad."""
        salida = []
        Param = self.env["ags.parametro"]
        Med = self.env["ags.medicion"]
        hay_dimension = bool(filtros.get("vendedor_id") or filtros.get("mercado_id"))

        # Indicadores en rojo contra el benchmark.
        # Son agregados de empresa: cuando hay filtro por dimension se omiten
        # en lugar de mostrarse como si pertenecieran al vendedor elegido.
        if not hay_dimension:
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
        dom_metas = [
            ("fecha_cierre", "=", cierre),
            ("semaforo", "=", "rojo"),
            ("estado", "in", ["aprobada", "cerrada"]),
        ]
        if filtros.get("vendedor_id"):
            dom_metas.append(("vendedor_id", "=", filtros["vendedor_id"]))
        if filtros.get("mercado_id"):
            dom_metas.append(("mercado_id", "=", filtros["mercado_id"]))
        for t in self.env["ags.meta"].search(dom_metas):
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

        # Clientes que destruyen valor.
        # El modelo no tiene ACL para el delegado a proposito: consultarlo sin
        # permiso lanzaria AccessError, asi que ni se intenta.
        dom_rent = [
            ("fecha_periodo", "=", cierre),
            ("destruye_valor", "=", True),
        ]
        if filtros.get("vendedor_id"):
            dom_rent.append(("vendedor_id", "=", filtros["vendedor_id"]))
        if filtros.get("mercado_id"):
            dom_rent.append(("mercado_id", "=", filtros["mercado_id"]))
        rentables = (
            self.env["ags.rentabilidad"].search(
                dom_rent, order="margen_economico", limit=5)
            if self._es_gerencia() else self.env["ags.rentabilidad"].browse()
        )
        for r in rentables:
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
        if not hay_dimension:
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

    # Una alerta sigue viva mientras nadie la atienda ni la descarte. El
    # estado "enviada" cuenta como viva: que el digest haya salido por correo
    # no significa que el problema este resuelto.
    ESTADOS_VIVOS = ("nueva", "enviada")

    @api.model
    def _alertas(self, cierre, filtros, limite=15):
        """Alertas vivas del periodo, listas para el drill-down."""
        dominio = [
            ("fecha_periodo", "=", cierre),
            ("estado", "in", list(self.ESTADOS_VIVOS)),
        ]
        if filtros.get("vendedor_id"):
            dominio.append(("responsable_id", "=", filtros["vendedor_id"]))
        registros = self.env["ags.alerta"].search(
            dominio, order="prioridad, periodos_seguidos desc", limit=limite)
        prioridades = dict(self.env["ags.alerta"]._fields["prioridad"].selection)
        tipos = dict(self.env["ags.alerta"]._fields["tipo"].selection)
        salida = []
        for a in registros:
            unidad = a.parametro_id.unidad if a.parametro_id else False

            # A donde lleva el clic. Se resuelve en el servidor porque es el
            # unico lado que sabe que relacion trae cada tipo de alerta.
            if a.partner_id:
                destino = ("res.partner", a.partner_id.id, a.partner_id.display_name)
            elif a.meta_id:
                destino = ("ags.meta", a.meta_id.id, _("Meta"))
            elif a.parametro_id:
                destino = ("ags.parametro", a.parametro_id.id, a.parametro_id.name)
            else:
                destino = (False, False, "")

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
                "estado": a.estado,
                "parametro_id": a.parametro_id.id or False,
                "responsable": a.responsable_id.name or "",
                "valor": self._formato(a.valor, unidad) if unidad else "",
                "referencia": (self._formato(a.valor_referencia, unidad)
                               if unidad and a.valor_referencia else ""),
                "modelo_destino": destino[0],
                "res_id_destino": destino[1],
                "nombre_destino": destino[2],
            })
        return salida

    @api.model
    def cerrar_alerta(self, alerta_id, accion, fecha=None, filtros=None):
        """Marca una alerta como atendida o descartada y devuelve el payload.

        Se cierra desde el cockpit, sin abrir el formulario: si atender una
        alerta cuesta tres clics y un cambio de pantalla, la lista se queda
        llena de cosas ya resueltas y deja de significar nada.
        """
        alerta = self.env["ags.alerta"].browse(int(alerta_id)).exists()
        if alerta:
            if accion == "atendida":
                alerta.action_atendida()
            elif accion == "descartada":
                alerta.action_descartada()
        return self.datos(fecha, filtros)

    @api.model
    def generar_alertas(self, fecha=None, filtros=None):
        """Corre los detectores del periodo y devuelve el payload actualizado."""
        if isinstance(fecha, str) and fecha:
            fecha = fields.Date.to_date(fecha)
        self.env["ags.alerta"].generar(fecha)
        return self.datos(fecha, filtros)

    # ------------------------------------------------------------------
    # Ventas del mes contra meta
    # ------------------------------------------------------------------

    @api.model
    def _ventas_vs_meta(self, cierre, filtros):
        """Ventas del mes contra meta.

        Un delegado ve su propia venta, no la de sus companeros: el ranking
        completo del equipo es informacion de Gerencia. El filtro se aplica
        en el dominio y no recortando el resultado, para que el total tampoco
        delate la venta de los demas.
        """
        desde = cierre.replace(day=1)
        propio = [] if self._es_gerencia() else [
            ("invoice_user_id", "=", self.env.user.id)]
        try:
            facturas = self.env["account.move"].search([
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("invoice_date", ">=", desde),
                ("invoice_date", "<=", cierre),
            ] + propio + self._dominio_facturas(filtros))
        except AccessError:
            return {
                "real": "", "real_num": 0.0, "meta": "", "meta_num": 0.0,
                "cumplimiento": 0.0, "hay_meta": False, "por_vendedor": [],
                "alcance": "sin_acceso",
            }
        real = sum(facturas.mapped("amount_untaxed"))

        dom_metas = [
            ("fecha_cierre", "=", cierre),
            ("dimension", "=", "vendedor"),
            ("estado", "in", ["aprobada", "cerrada"]),
        ]
        if filtros.get("vendedor_id"):
            dom_metas.append(("vendedor_id", "=", filtros["vendedor_id"]))
        metas = self.env["ags.meta"].search(dom_metas)
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
            "alcance": "filtrado" if filtros else "empresa",
        }

    # ------------------------------------------------------------------
    # Punto de entrada
    # ------------------------------------------------------------------

    @api.model
    def datos(self, fecha=None, filtros=None):
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
        filtros = self._normalizar(filtros)
        cierre = self._cierre_mes(fecha)
        previo = self._cierre_mes(fecha, atras=1)
        homologo = self._cierre_mes(fecha, atras=12)

        excepciones = self._excepciones(cierre, filtros)
        return {
            "contrato": CONTRATO,
            # strftime usa el locale del servidor y devolvia "August 2026" en
            # una pantalla que el gerente lee en espanol. format_date respeta
            # el idioma del usuario.
            "periodo": format_date(self.env, cierre, date_format="MMMM y").capitalize(),
            "periodo_label": format_date(self.env, cierre, date_format="MMM y"),
            "previo_label": format_date(self.env, previo, date_format="MMM y"),
            "homologo_label": format_date(self.env, homologo, date_format="MMM y"),
            "cierre": cierre.isoformat(),
            "previo": previo.isoformat(),
            "homologo": homologo.isoformat(),
            "filtros": filtros,
            "filtros_activos": self._etiquetas_filtros(filtros),
            "hay_filtro": bool(filtros),
            # Las zonas que salen de ags.medicion son agregados de empresa y
            # no responden al filtro. Se declara aqui para que el front lo
            # selle en pantalla en vez de dejar que el gerente lo suponga.
            "alcance_agregado": "empresa",
            "es_gerencia": self._es_gerencia(),
            "confianza": self._banda_confianza(cierre, filtros),
            "excepciones": excepciones,
            "n_excepciones": len([e for e in excepciones
                                  if e["gravedad"] in ("danger", "warning")]),
            "ejecutivo": self._ejecutivo(cierre, previo, homologo),
            "bloques": self._bloques(cierre, homologo),
            "alertas": self._alertas(cierre, filtros),
            "ventas": self._ventas_vs_meta(cierre, filtros),
            "ejes": EJES,
        }

    # ------------------------------------------------------------------
    # Series temporales
    # ------------------------------------------------------------------

    @api.model
    def serie(self, codigo, meses=24, fecha=None):
        """Historia de un indicador con sus referencias.

        Devuelve los puntos y, por separado, las bandas del benchmark y el
        baseline. El grafico las dibuja de fondo en lugar de trazarlas como
        una linea mas: una banda de fondo se lee como territorio -- estoy
        dentro o fuera -- mientras que una linea invita a compararla contra
        la serie punto por punto, que no es la pregunta.

        Los periodos atipicos viajan marcados para que el front los pinte
        distinto: son datos reales, pero no representan la operacion normal.
        """
        param = self.env["ags.parametro"].search([("codigo", "=", codigo)], limit=1)
        if not param:
            return {}
        if isinstance(fecha, str) and fecha:
            fecha = fields.Date.to_date(fecha)
        cierre = self._cierre_mes(fecha)
        desde = cierre.replace(day=1) - relativedelta(months=meses - 1)

        mediciones = self.env["ags.medicion"].search([
            ("parametro_id", "=", param.id),
            ("fecha_periodo", ">=", desde),
            ("fecha_periodo", "<=", cierre),
        ], order="fecha_periodo asc")

        puntos = [{
            "fecha": m.fecha_periodo.isoformat(),
            "label": format_date(self.env, m.fecha_periodo, date_format="MMM yy"),
            "valor": m.valor,
            "texto": self._formato(m.valor, param.unidad),
            "semaforo": m.semaforo or "sin_dato",
            "atipico": m.periodo_atipico,
        } for m in mediciones]

        hoy = fields.Date.context_today(self)
        benchmark = param.benchmark_ids.filtered(
            lambda b: b.vigente_desde and b.vigente_desde <= hoy
            and (not b.vigente_hasta or b.vigente_hasta >= hoy)
        )[:1]
        baseline = param.baseline_ids.filtered(lambda b: b.vigente)[:1]

        return {
            "codigo": param.codigo,
            "nombre": param.name,
            "unidad": param.unidad,
            "direccion": param.direccion,
            "puntos": puntos,
            "n_puntos": len(puntos),
            "banda": {
                "hay_dato": bool(benchmark),
                "minimo": benchmark.valor_minimo if benchmark else 0.0,
                "objetivo": benchmark.valor_objetivo if benchmark else 0.0,
                "clase_mundial": benchmark.valor_clase_mundial if benchmark else 0.0,
                "objetivo_texto": self._formato(
                    benchmark.valor_objetivo, param.unidad) if benchmark else "",
            },
            "baseline": {
                "hay_dato": bool(baseline),
                "valor": baseline.valor if baseline else 0.0,
                "texto": self._formato(
                    baseline.valor, param.unidad) if baseline else "",
            },
        }

    # ------------------------------------------------------------------
    # Trazabilidad
    # ------------------------------------------------------------------

    @api.model
    def desglose(self, codigo, fecha=None):
        """Piezas que forman el valor de un indicador en el periodo.

        Devuelve el desglose guardado en el momento del calculo, no una
        reconstruccion: si los asientos cambiaron despues, el desglose sigue
        mostrando contra que se calculo aquel dia, que es lo que permite
        auditarlo.
        """
        param = self.env["ags.parametro"].search(
            [("codigo", "=", codigo)], limit=1)
        if not param:
            return {}
        if isinstance(fecha, str) and fecha:
            fecha = fields.Date.to_date(fecha)
        cierre = self._cierre_mes(fecha)
        medicion = self.env["ags.medicion"].search([
            ("parametro_id", "=", param.id),
            ("fecha_periodo", "=", cierre),
        ], limit=1)
        if not medicion:
            return {"hay_dato": False, "nombre": param.name}

        piezas = []
        for c in medicion.componente_ids:
            piezas.append({
                "id": c.id,
                "rol": c.rol,
                "tipo": c.tipo,
                "valor": self._formato(c.valor, param.unidad)
                         if c.tipo == "derivado" else "{:,.2f}".format(c.valor),
                "cuentas": c.cuentas_resumen or "",
                "cuentas_detalle": c.cuentas_codigos or "",
                "desde": c.fecha_desde.isoformat() if c.fecha_desde else "",
                "hasta": c.fecha_hasta.isoformat() if c.fecha_hasta else "",
                "notas": c.notas or "",
                "tiene_origen": bool(c.cuenta_ids) or bool(c.modelo and c.dominio),
            })

        return {
            "hay_dato": True,
            "nombre": param.name,
            "codigo": param.codigo,
            "medicion_id": medicion.id,
            "valor": self._formato(medicion.valor, param.unidad),
            "metodo": param.metodo_calculo or "",
            "notas": medicion.notas or "",
            "componentes": piezas,
        }

    @api.model
    def abrir_origen(self, componente_id):
        """Delega en el componente la apertura de sus registros de origen."""
        componente = self.env["ags.componente"].browse(
            int(componente_id)).exists()
        if not componente:
            return False
        return componente.action_ver_origen()

    @api.model
    def recalcular(self, fecha=None, filtros=None):
        """Recalcula el periodo desde el cockpit, sin salir de la pantalla."""
        if isinstance(fecha, str) and fecha:
            fecha = fields.Date.to_date(fecha)
        f = fecha or fields.Date.context_today(self)
        self.env["ags.calculador"].calcular_periodo(f)
        self.env["ags.rentabilidad"].calcular_periodo(f)
        return self.datos(fecha, filtros)
