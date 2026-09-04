# -*- coding: utf-8 -*-
"""Circuito de aviso de la auditoria (Etapa 8.8).

TRES DECISIONES DE DISENO, y las tres son sobre atencion y no sobre correo:

D27 - EL AVISO CALLA CUANDO NO HAY NOVEDAD. Un resumen que llega todos los
      dias con la misma lista de 33 hallazgos deja de leerse en una semana, y
      esa atencion no se recupera. Se escribe cuando algo NACE, REINCIDE o se
      CORRIGE. El silencio es informacion: significa que nada cambio.

D28 - PARA QUE EL SILENCIO SIGNIFIQUE ALGO, TIENE QUE ROMPERSE SOLO. Un dia
      fijo a la semana se manda el estado aunque no haya movimiento. Sin eso
      no hay forma de distinguir "nada cambio" de "el cron lleva un mes
      caido", que fue exactamente lo que paso con las mediciones en julio.

D29 - LA CORRECCION SE AVISA IGUAL QUE EL HALLAZGO. Un circuito que solo
      notifica lo que esta mal entrena a la gente a no abrir el correo. Que
      algo se cerro solo es la unica prueba de que el trabajo de ayer sirvio.

El escalado es uno solo: un hallazgo grave que lleva mas dias abiertos que
ags.config.auditoria_dias_vencido vuelve al aviso aunque no haya cambiado. No
sube de gravedad -- la gravedad la declara la regla (D14) -- solo reaparece.
"""
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)

ETIQUETA_GRAVEDAD = {
    "danger": "Grave",
    "warning": "Advertencia",
    "info": "Informativo",
}


class AgsAuditorAviso(models.AbstractModel):
    _inherit = "ags.auditor"

    # ==================================================================
    # Entrada
    # ==================================================================

    @api.model
    def notificar_novedad(self, forzar=False):
        """Escribe a cada responsable lo que cambio hoy en su terreno.

        Devuelve el numero de avisos enviados. Cero es un resultado normal y
        no un fallo: quiere decir que hoy no nacio, no reincidio y no se
        corrigio nada.
        """
        cfg = self.env["ags.config"].get_config()
        hoy = fields.Date.context_today(self)
        Hallazgo = self.env["ags.hallazgo"]

        nacidos = Hallazgo.search([
            ("vivo", "=", True), ("primera_deteccion", "=", hoy)])
        reincidentes = Hallazgo.search([
            ("vivo", "=", True), ("ultima_reincidencia", "=", hoy)])
        corregidos = Hallazgo.search([
            ("estado", "=", "cerrado_auto"), ("fecha_resolucion", "=", hoy)])

        hubo_movimiento = bool(nacidos or reincidentes or corregidos)
        dia_resumen = cfg.auditoria_dia_resumen or ""
        es_dia_de_resumen = dia_resumen != "" and str(hoy.weekday()) == dia_resumen

        if not (forzar or hubo_movimiento or es_dia_de_resumen):
            _logger.info(
                "ags.auditor: sin novedad el %s, no se envia aviso", hoy)
            return 0

        # Los vencidos solo viajan cuando el correo ya sale por otra razon.
        # Un hallazgo grave que lleva 40 dias abierto no es noticia de hoy:
        # es el recordatorio que acompana a la noticia.
        dias = cfg.auditoria_dias_vencido or 0
        vencidos = Hallazgo.browse()
        if dias > 0:
            vencidos = Hallazgo.search([
                ("vivo", "=", True), ("gravedad", "=", "danger"),
            ]).filtered(lambda h: h.dias_abierto > dias)

        buzones = self._repartir(
            cfg, nacidos, reincidentes, corregidos, vencidos)

        enviados = 0
        for usuario, secciones in buzones.items():
            cuerpo = self._cuerpo_aviso(usuario, secciones, hoy, cfg)
            if not cuerpo:
                continue
            self._enviar(usuario, self._asunto(secciones, hoy), cuerpo)
            enviados += 1

        _logger.info(
            "ags.auditor: aviso enviado a %s destinatario(s) "
            "(nacidos %s, reincidentes %s, corregidos %s)",
            enviados, len(nacidos), len(reincidentes), len(corregidos))
        return enviados

    # ==================================================================
    # Reparto
    # ==================================================================

    @api.model
    def _repartir(self, cfg, nacidos, reincidentes, corregidos, vencidos):
        """Agrupa por quien tiene que hacer algo con cada hallazgo.

        El responsable se hereda de la regla y se puede reasignar caso por
        caso; lo que no tiene responsable cae en los destinatarios de la
        configuracion. Una lista vacia no reparte nada: el aviso sin
        destinatario no se envia a nadie 'por si acaso', se registra en el
        log para que se note que falta configurar.
        """
        respaldo = cfg.auditoria_destinatario_ids
        if not respaldo:
            _logger.warning(
                "ags.auditor: no hay destinatarios configurados; los "
                "hallazgos sin responsable no se avisan a nadie.")

        buzones = {}
        secciones = (
            ("nacidos", nacidos),
            ("reincidentes", reincidentes),
            ("corregidos", corregidos),
            ("vencidos", vencidos),
        )
        for nombre, registros in secciones:
            for hallazgo in registros:
                destinos = hallazgo.responsable_id or respaldo
                for usuario in destinos:
                    buzon = buzones.setdefault(usuario, {})
                    buzon.setdefault(nombre, self.env["ags.hallazgo"].browse())
                    buzon[nombre] |= hallazgo
        return buzones

    # ==================================================================
    # Redaccion
    # ==================================================================

    @api.model
    def _asunto(self, secciones, hoy):
        n = len(secciones.get("nacidos", []))
        r = len(secciones.get("reincidentes", []))
        c = len(secciones.get("corregidos", []))
        partes = []
        if n:
            partes.append(_("%s nuevo(s)") % n)
        if r:
            partes.append(_("%s reincidente(s)") % r)
        if c:
            partes.append(_("%s corregido(s)") % c)
        if not partes:
            return _("Auditoria AG: sin novedad al %s") % hoy
        return _("Auditoria AG %s: %s") % (hoy, ", ".join(partes))

    @api.model
    def _cuerpo_aviso(self, usuario, secciones, hoy, cfg):
        bloques = [
            ("nacidos", _("Aparecio hoy"), "#b3261e"),
            ("reincidentes", _("Volvio a aparecer"), "#8c4a00"),
            ("corregidos", _("Se corrigio"), "#1b7f3b"),
            ("vencidos", _("Grave y abierto hace mas de %s dias")
             % (cfg.auditoria_dias_vencido or 0), "#5f5f5f"),
        ]
        cuerpo = []
        for clave, titulo, color in bloques:
            registros = secciones.get(clave)
            if not registros:
                continue
            cuerpo.append(
                '<h3 style="color:%s;margin:16px 0 6px">%s (%s)</h3>'
                % (color, titulo, len(registros)))
            cuerpo.append(self._tabla(registros, clave == "corregidos"))

        if not cuerpo:
            return ""

        vivos = self.env["ags.hallazgo"].search_count([("vivo", "=", True)])
        encabezado = _(
            "<p>%(saludo)s,</p>"
            "<p>Resumen de la auditoria del %(fecha)s. "
            "Hoy quedan <b>%(vivos)s</b> hallazgos abiertos en total.</p>"
        ) % {"saludo": usuario.name, "fecha": hoy, "vivos": vivos}

        pie = _(
            "<p style='color:#666;font-size:12px;margin-top:20px'>"
            "Este aviso solo se escribe cuando algo cambia. Si no llega, no "
            "cambio nada. El expediente completo esta en "
            "Inteligencia &gt; Auditoria &gt; Hallazgos.</p>")

        return encabezado + "".join(cuerpo) + pie

    @api.model
    def _tabla(self, registros, cerrado=False):
        filas = []
        for h in registros:
            filas.append(
                "<tr>"
                '<td style="padding:4px 8px;border-bottom:1px solid #eee">'
                "<b>%s</b></td>"
                '<td style="padding:4px 8px;border-bottom:1px solid #eee">'
                "%s</td>"
                '<td style="padding:4px 8px;border-bottom:1px solid #eee">'
                "%s</td>"
                '<td style="padding:4px 8px;border-bottom:1px solid #eee;'
                'text-align:right">%s</td>'
                '<td style="padding:4px 8px;border-bottom:1px solid #eee">'
                "%s</td>"
                "</tr>" % (
                    h.codigo_regla or "",
                    h.sujeto or "",
                    h.compania_id.name or "",
                    h.cantidad or 0,
                    ETIQUETA_GRAVEDAD.get(h.gravedad, h.gravedad or ""),
                ))
        cabecera = (
            '<tr style="background:#f5f5f5;text-align:left">'
            '<th style="padding:4px 8px">Regla</th>'
            '<th style="padding:4px 8px">Sujeto</th>'
            '<th style="padding:4px 8px">Compania</th>'
            '<th style="padding:4px 8px;text-align:right">Registros</th>'
            '<th style="padding:4px 8px">Gravedad</th></tr>')
        return (
            '<table style="border-collapse:collapse;width:100%%;'
            'font-size:13px">%s%s</table>' % (cabecera, "".join(filas)))

    # ==================================================================
    # Envio
    # ==================================================================

    @api.model
    def _enviar(self, usuario, asunto, cuerpo):
        """Un correo por destinatario.

        No se usa message_post sobre un hallazgo: el aviso habla de varios a
        la vez y colgarlo del primero de la lista dejaria el expediente de ese
        hallazgo contando cosas de otros. El expediente individual se escribe
        en su propio chatter cuando nace o reincide.
        """
        if not usuario.partner_id:
            return False
        correo = self.env["mail.mail"].sudo().create({
            "subject": asunto,
            "body_html": cuerpo,
            "recipient_ids": [(4, usuario.partner_id.id)],
            "auto_delete": False,
        })
        correo.send()
        return True

    # ==================================================================
    # Cron
    # ==================================================================

    @api.model
    def cron_auditoria_diaria(self):
        """Evalua las reglas diarias y avisa lo que cambio.

        El orden importa: primero se reconcilian los hallazgos, despues se
        lee lo que cambio. Al reves se estaria avisando el resultado de ayer.
        """
        resumen = self.evaluar_reglas(frecuencia="diaria")
        try:
            self.notificar_novedad()
        except Exception:
            # Un fallo de correo no puede tumbar la auditoria: lo detectado
            # ya esta guardado y es lo que no se puede perder.
            _logger.exception("ags.auditor: fallo el aviso de auditoria")
        return resumen
