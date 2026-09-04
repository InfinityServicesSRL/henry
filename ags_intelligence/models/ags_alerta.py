# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AgsAlerta(models.Model):
    """Alertas por deterioro de indicadores.

    PRINCIPIO DE DISENO: el valor de una alerta es inversamente proporcional
    a cuantas se envian. Tres reglas la protegen de volverse ruido:

      1. NO SE ALERTA SOBRE DATOS NO CONFIABLES. Un indicador cuya serie aun
         no madura puede mostrar cualquier numero. Alertar sobre eso ensena a
         la gente a ignorar las alertas, y despues no se puede recuperar la
         atencion cuando importe de verdad.

      2. NO SE REPITE LO YA AVISADO. Si un indicador sigue en rojo y nada
         cambio, no se vuelve a alertar: se escala la prioridad. Repetir el
         mismo mensaje cada mes convierte la alerta en fondo.

      3. SE PRIORIZA Y SE CORTA. Cada destinatario recibe las mas relevantes
         de su ambito, no todas. Una lista de treinta puntos no se lee.

    El deterioro sostenido es lo mas valioso que detecta: un indicador que
    empeora cinco por ciento cada mes nunca cruza un umbral, y es justo el
    que nadie ve venir.
    """
    _name = "ags.alerta"
    _description = "AG Intelligence - Alerta"
    _inherit = ["mail.thread"]
    _order = "fecha_periodo desc, prioridad, id"
    _rec_name = "titulo"

    fecha_periodo = fields.Date(string="Periodo", required=True, index=True)
    tipo = fields.Selection(
        [
            ("umbral", "Cruzo el umbral"),
            ("deterioro", "Deterioro sostenido"),
            ("meta", "Meta incumplida"),
            ("cliente", "Cliente que destruye valor"),
            ("registro", "Calidad del registro"),
        ],
        string="Tipo", required=True, index=True,
    )
    prioridad = fields.Selection(
        [("1", "Alta"), ("2", "Media"), ("3", "Informativa")],
        string="Prioridad", required=True, default="2", index=True,
    )
    titulo = fields.Char(string="Alerta", required=True)
    detalle = fields.Text(string="Detalle")
    recomendacion = fields.Text(string="Que revisar")

    parametro_id = fields.Many2one("ags.parametro", string="Parametro", index=True)
    meta_id = fields.Many2one("ags.meta", string="Meta")
    partner_id = fields.Many2one("res.partner", string="Cliente")
    responsable_id = fields.Many2one("res.users", string="Responsable", index=True)

    valor = fields.Float(string="Valor", digits=(16, 2))
    valor_referencia = fields.Float(string="Referencia", digits=(16, 2))
    periodos_seguidos = fields.Integer(
        string="Periodos consecutivos", default=1,
        help="Cuantos periodos lleva la condicion. Un valor creciente escala "
             "la prioridad de la alerta.",
    )

    estado = fields.Selection(
        [
            ("nueva", "Nueva"),
            ("enviada", "Enviada"),
            ("atendida", "Atendida"),
            ("descartada", "Descartada"),
        ],
        string="Estado", default="nueva", required=True, tracking=True,
    )
    fecha_envio = fields.Datetime(string="Enviada el", readonly=True)
    nota_cierre = fields.Text(string="Nota de cierre", tracking=True)

    clave = fields.Char(
        string="Clave",
        index=True,
        help="Identificador de la condicion, para no repetir la misma alerta.",
    )

    _sql_constraints = [
        ("alerta_unica", "unique(fecha_periodo, clave)",
         "Ya existe esa alerta para el periodo."),
    ]

    # ==================================================================
    # DETECCION
    # ==================================================================

    @api.model
    def _cierre(self, fecha=None, atras=0):
        f = fecha or fields.Date.context_today(self)
        primero = f.replace(day=1) - relativedelta(months=atras)
        return primero + relativedelta(months=1, days=-1)

    @api.model
    def _detectar_umbral(self, cierre, previo):
        """Indicadores que pasaron a rojo este periodo.

        Solo los que CRUZARON: si ya estaba en rojo el mes pasado, no es
        noticia nueva y se maneja como deterioro sostenido.
        """
        Med = self.env["ags.medicion"]
        salida = []
        rojos = Med.search([
            ("fecha_periodo", "=", cierre),
            ("semaforo", "=", "rojo"),
            ("periodo_atipico", "=", False),
        ])
        for m in rojos:
            p = m.parametro_id
            if p.madurez != "confiable":
                continue
            ant = Med.search([
                ("parametro_id", "=", p.id), ("fecha_periodo", "=", previo),
            ], limit=1)
            if ant and ant.semaforo == "rojo":
                continue  # ya estaba mal, no es cruce
            bm = p.benchmark_vigente_id
            salida.append({
                "clave": "umbral:%s" % p.codigo,
                "tipo": "umbral",
                "prioridad": "1",
                "titulo": "%s salio de rango" % p.name,
                "detalle": "Paso de %s a %s. El minimo aceptable es %s." % (
                    ant.valor if ant else "sin dato", m.valor,
                    bm.valor_minimo if bm else "-"),
                "recomendacion": p.metodo_calculo or "",
                "parametro_id": p.id,
                "responsable_id": p.responsable_id.id or False,
                "valor": m.valor,
                "valor_referencia": bm.valor_minimo if bm else 0.0,
            })
        return salida

    @api.model
    def _detectar_deterioro(self, cierre, minimo=3):
        """Indicadores que empeoran de forma sostenida sin cruzar umbral.

        Es el hallazgo mas valioso del sistema: un indicador que pierde tres
        por ciento cada mes nunca dispara un semaforo, y a los seis meses el
        problema ya es estructural. Nadie lo ve venir mirando el valor del mes.
        """
        Med = self.env["ags.medicion"]
        salida = []
        for p in self.env["ags.parametro"].search([
            ("madurez", "=", "confiable"),
            ("direccion", "!=", "neutro"),
        ]):
            serie = Med.search([
                ("parametro_id", "=", p.id),
                ("fecha_periodo", "<=", cierre),
                ("periodo_atipico", "=", False),
            ], order="fecha_periodo desc", limit=minimo + 1)
            if len(serie) < minimo + 1:
                continue
            vals = list(reversed(serie.mapped("valor")))
            peor = p.direccion == "menor_mejor"
            seguidos = 0
            for i in range(1, len(vals)):
                empeora = vals[i] > vals[i - 1] if peor else vals[i] < vals[i - 1]
                if empeora:
                    seguidos += 1
                else:
                    seguidos = 0
            if seguidos < minimo:
                continue
            total = ((vals[-1] - vals[0]) / abs(vals[0]) * 100.0) if vals[0] else 0.0
            salida.append({
                "clave": "deterioro:%s" % p.codigo,
                "tipo": "deterioro",
                "prioridad": "1" if abs(total) > 20 else "2",
                "titulo": "%s se deteriora desde hace %s periodos" % (
                    p.name, seguidos),
                "detalle": ("Paso de %s a %s, una variacion de %.1f%%. "
                            "No ha cruzado el umbral, pero la tendencia es "
                            "sostenida.") % (
                    round(vals[0], 2), round(vals[-1], 2), total),
                "recomendacion": _("Revisar antes de que cruce el umbral."),
                "parametro_id": p.id,
                "responsable_id": p.responsable_id.id or False,
                "valor": vals[-1],
                "valor_referencia": vals[0],
                "periodos_seguidos": seguidos,
            })
        return salida

    @api.model
    def _detectar_metas(self, cierre):
        salida = []
        for t in self.env["ags.meta"].search([
            ("fecha_cierre", "=", cierre),
            ("semaforo", "=", "rojo"),
            ("estado", "in", ["aprobada", "cerrada"]),
        ]):
            quien = ""
            resp = False
            if t.dimension == "vendedor" and t.vendedor_id:
                quien = " de %s" % t.vendedor_id.name
                resp = t.vendedor_id.id
            elif t.dimension == "mercado" and t.mercado_id:
                quien = " en %s" % t.mercado_id.name
            salida.append({
                "clave": "meta:%s" % t.id,
                "tipo": "meta",
                "prioridad": "1" if t.cumplimiento < 70 else "2",
                "titulo": "Meta incumplida: %s%s" % (t.parametro_id.name, quien),
                "detalle": "Cumplimiento de %.0f%%: real %s frente a meta %s." % (
                    t.cumplimiento, round(t.valor_real, 2), round(t.valor, 2)),
                "meta_id": t.id,
                "parametro_id": t.parametro_id.id,
                "responsable_id": resp or t.parametro_id.responsable_id.id or False,
                "valor": t.valor_real,
                "valor_referencia": t.valor,
            })
        return salida

    @api.model
    def _detectar_clientes(self, cierre, minimo=50000):
        """Clientes que destruyen valor, con corte por materialidad.

        El corte existe porque un cliente de RD$ 1,900 con margen economico
        negativo es cierto y es irrelevante. Alertar sobre eso gasta atencion
        que hara falta cuando el caso sea grande.
        """
        salida = []
        for r in self.env["ags.rentabilidad"].search([
            ("fecha_periodo", "=", cierre),
            ("destruye_valor", "=", True),
            ("ventas", ">=", minimo),
        ], order="margen_economico"):
            salida.append({
                "clave": "cliente:%s" % r.partner_id.id,
                "tipo": "cliente",
                "prioridad": "1",
                "titulo": "%s destruye valor" % r.partner_id.display_name,
                "detalle": ("Ventas de %s con margen bruto de %.1f%%, cobro a "
                            "%.0f dias y margen economico de %s.") % (
                    "{:,.0f}".format(r.ventas), r.margen_pct,
                    r.dias_cobro, "{:,.0f}".format(r.margen_economico)),
                "recomendacion": _("Revisar precio, plazo y notas de credito."),
                "partner_id": r.partner_id.id,
                "responsable_id": r.vendedor_id.id or False,
                "valor": r.margen_economico,
            })
        return salida

    @api.model
    def _detectar_registro(self, cierre):
        """Indicadores de calidad del registro por encima de lo tolerable."""
        Med = self.env["ags.medicion"]
        salida = []
        vigilados = {
            "NC_SIN_CLASIFICAR": 20.0,
            "VENTAS_SIN_TERMINO": 15.0,
            "COMPRAS_SIN_TERMINO": 40.0,
            "AJUSTES_REVERSADOS": 10.0,
            "INGRESOS_SIN_FACTURA": 2.0,
        }
        for codigo, tope in vigilados.items():
            p = self.env["ags.parametro"].search([("codigo", "=", codigo)], limit=1)
            if not p:
                continue
            m = Med.search([
                ("parametro_id", "=", p.id), ("fecha_periodo", "=", cierre),
            ], limit=1)
            if not m or m.valor <= tope:
                continue
            salida.append({
                "clave": "registro:%s" % codigo,
                "tipo": "registro",
                "prioridad": "2",
                "titulo": "%s por encima de lo tolerable" % p.name,
                "detalle": "%.1f%% frente a un tope de %.0f%%. %s" % (
                    m.valor, tope, m.notas or ""),
                "recomendacion": _("Mientras esta cifra sea alta, los "
                                   "indicadores que dependen de ella no son "
                                   "confiables."),
                "parametro_id": p.id,
                "responsable_id": p.responsable_id.id or False,
                "valor": m.valor,
                "valor_referencia": tope,
            })

        # Un indicador que se quedo sin registros que lo respalden. No vale
        # cero: no vale. Se avisa alto solo cuando es un CAMBIO -- el mes
        # pasado si habia registros y este no --, porque la condicion cronica
        # ya escala sola a los tres periodos y repetirla cada mes la vuelve
        # fondo, que es justo lo que este modelo evita por diseno.
        previo = cierre.replace(day=1) - relativedelta(days=1)
        for m in Med.search([
            ("fecha_periodo", "=", cierre),
            ("sin_evidencia", "=", True),
        ]):
            p = m.parametro_id
            antes = Med.search([
                ("parametro_id", "=", p.id),
                ("fecha_periodo", "=", previo),
            ], limit=1)
            cambio = bool(antes and not antes.sin_evidencia)
            salida.append({
                "clave": "sin_evidencia:%s" % p.codigo,
                "tipo": "registro",
                "prioridad": "1" if cambio else "2",
                "titulo": (
                    "%s se quedo sin registros que lo respalden" % p.name
                    if cambio else
                    "%s sigue sin registros que lo respalden" % p.name),
                "detalle": ("%s El valor calculado es cero porque no hay nada "
                            "que medir, no porque el resultado sea bueno." % (
                                m.notas or "")).strip(),
                "recomendacion": _("Mientras no se registre, este indicador y "
                                   "los que dependen de el no significan nada."),
                "parametro_id": p.id,
                "responsable_id": p.responsable_id.id or False,
                "valor": m.valor,
            })
        return salida

    # ==================================================================
    # GENERACION
    # ==================================================================

    @api.model
    def generar(self, fecha=None):
        """Detecta y crea las alertas del periodo, sin duplicar."""
        cierre = self._cierre(fecha)
        previo = self._cierre(fecha, atras=1)

        candidatas = []
        candidatas += self._detectar_umbral(cierre, previo)
        candidatas += self._detectar_deterioro(cierre)
        candidatas += self._detectar_metas(cierre)
        candidatas += self._detectar_clientes(cierre)
        candidatas += self._detectar_registro(cierre)

        existentes = set(self.search([
            ("fecha_periodo", "=", cierre)]).mapped("clave"))
        nuevas = []
        for c in candidatas:
            if c["clave"] in existentes:
                continue
            # Escalar prioridad si la condicion viene del periodo anterior
            anterior = self.search([
                ("clave", "=", c["clave"]), ("fecha_periodo", "=", previo),
            ], limit=1)
            if anterior:
                c["periodos_seguidos"] = (anterior.periodos_seguidos or 1) + 1
                if c["periodos_seguidos"] >= 3 and c["prioridad"] != "1":
                    c["prioridad"] = "1"
                    c["titulo"] = "%s (%s periodos sin resolver)" % (
                        c["titulo"], c["periodos_seguidos"])
            c["fecha_periodo"] = cierre
            nuevas.append(c)

        creadas = self.create(nuevas) if nuevas else self.browse()
        _logger.info("Alertas %s: %s creadas de %s candidatas",
                     cierre, len(creadas), len(candidatas))
        return creadas

    # ==================================================================
    # DIGEST
    # ==================================================================

    @api.model
    def _destinatarios(self, alertas):
        """Agrupa por responsable. Las sin responsable van a Gerencia."""
        grupos = {}
        gerencia = self.env.ref(
            "ags_intelligence.group_ags_manager", raise_if_not_found=False)
        fallback = gerencia.users[:1] if gerencia and gerencia.users else self.env.user
        for a in alertas:
            u = a.responsable_id or fallback
            grupos.setdefault(u, self.browse())
            grupos[u] |= a
        return grupos

    def _html_digest(self, usuario, tope=8):
        """Arma el cuerpo del correo, priorizado y cortado.

        El tope existe por diseno: una lista de treinta alertas no se lee. Si
        hay mas, se dice cuantas quedaron fuera y se enlaza al listado.
        """
        orden = sorted(self, key=lambda a: (a.prioridad, -abs(a.valor or 0)))
        muestra = orden[:tope]
        resto = len(orden) - len(muestra)
        etiquetas = dict(self._fields["prioridad"].selection)
        colores = {"1": "#dc3545", "2": "#ffc107", "3": "#17a2b8"}

        filas = []
        for a in muestra:
            filas.append("""
              <tr>
                <td style="padding:10px 12px;border-left:3px solid %s;
                           border-bottom:1px solid #eee;">
                  <div style="font-weight:600;color:#212529;">%s</div>
                  <div style="font-size:13px;color:#6c757d;margin-top:3px;">%s</div>
                  %s
                </td>
              </tr>""" % (
                colores.get(a.prioridad, "#6c757d"),
                a.titulo or "",
                a.detalle or "",
                ('<div style="font-size:12px;color:#856404;margin-top:5px;">%s</div>'
                 % a.recomendacion) if a.recomendacion else "",
            ))

        pie = ""
        if resto > 0:
            pie = ('<p style="font-size:13px;color:#6c757d;">Y %s alerta(s) mas '
                   'en Inteligencia &gt; Alertas.</p>' % resto)

        altas = len([a for a in orden if a.prioridad == "1"])
        return """
        <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;">
          <p style="margin:0 0 4px;font-size:15px;">%(saludo)s</p>
          <p style="margin:0 0 16px;font-size:13px;color:#6c757d;">
            %(resumen)s
          </p>
          <table style="width:100%%;border-collapse:collapse;
                        border:1px solid #eee;border-radius:4px;">
            %(filas)s
          </table>
          %(pie)s
          <p style="font-size:12px;color:#adb5bd;margin-top:18px;">
            Generado por AG Intelligence. Las alertas se emiten solo sobre
            indicadores con serie confiable.
          </p>
        </div>""" % {
            "saludo": _("Hola %s,") % (usuario.name or ""),
            "resumen": _("%(t)s punto(s) para revisar del periodo %(p)s, "
                         "%(a)s de prioridad alta.") % {
                "t": len(orden), "p": self[:1].fecha_periodo or "", "a": altas},
            "filas": "".join(filas),
            "pie": pie,
        }

    @api.model
    def enviar_digest(self, fecha=None, solo_prueba=False):
        """Envia un correo por destinatario con sus alertas pendientes."""
        cierre = self._cierre(fecha)
        pendientes = self.search([
            ("fecha_periodo", "=", cierre), ("estado", "=", "nueva"),
        ])
        if not pendientes:
            _logger.info("Alertas %s: nada por enviar", cierre)
            return 0

        enviados = 0
        for usuario, alertas in self._destinatarios(pendientes).items():
            if not usuario.email:
                _logger.warning("Alertas: %s no tiene correo", usuario.name)
                continue
            cuerpo = alertas._html_digest(usuario)
            if solo_prueba:
                _logger.info("PRUEBA digest para %s:\n%s", usuario.email, cuerpo)
                enviados += 1
                continue
            self.env["mail.mail"].create({
                "subject": _("AG Intelligence · %s punto(s) para revisar") % len(alertas),
                "email_to": usuario.email,
                "body_html": cuerpo,
                "auto_delete": False,
            }).send()
            alertas.write({
                "estado": "enviada",
                "fecha_envio": fields.Datetime.now(),
            })
            enviados += 1
        return enviados

    @api.model
    def cron_alertas_mensuales(self):
        """Genera y envia el digest del mes cerrado.

        Corre a inicios de mes sobre el periodo anterior, que ya esta completo.
        Alertar sobre un mes en curso produce falsos positivos: un indicador
        acumulado siempre se ve mal el dia 3.
        """
        anterior = self._cierre(atras=1)
        self.generar(anterior)
        return self.enviar_digest(anterior)

    # ==================================================================
    # ACCIONES
    # ==================================================================

    def action_atendida(self):
        for a in self:
            a.estado = "atendida"
        return True

    def action_descartada(self):
        for a in self:
            a.estado = "descartada"
        return True
