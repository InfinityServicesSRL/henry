# -*- coding: utf-8 -*-
"""Envío de reportes a contactos del cliente y a vendedores.

Reglas que este wizard hace cumplir, porque no son opcionales:

* **Al cliente nunca se le envía su grado ni su posición en el ranking.**
  Recibe hechos: sus facturas, sus saldos, sus fechas. La calificación es
  interna, y si se filtra la conversación deja de ser comercial.
* **Por WhatsApp va un enlace, no un adjunto.** Meta exige plantilla aprobada
  con encabezado de documento para mandar un PDF proactivo, y el archivo
  tiene que vivir en una URL pública de todos modos. El enlace con token
  caduca y se sabe si lo abrieron.
* **HTML y PDF salen de la misma plantilla QWeb.** Dos plantillas paralelas
  terminan desincronizadas.
"""

import base64
from urllib.parse import quote

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AgEnvioReporte(models.TransientModel):
    _name = "ag.envio.reporte"
    _inherit = "ag.correo.mixin"
    _description = "Enviar reporte de desempeño"

    partner_ids = fields.Many2many(
        "res.partner", string="Clientes", required=True)
    partner_count = fields.Integer(compute="_compute_resumen")

    tipo = fields.Selection([
        ("estado_cuenta", "Estado de cuenta (para el cliente)"),
        ("ficha", "Ficha de desempeño (interna)"),
        ("cartera", "Cartera consolidada (interna)"),
    ], string="Reporte", required=True, default="estado_cuenta")

    canal = fields.Selection([
        ("correo", "Correo con PDF adjunto"),
        ("whatsapp", "WhatsApp con enlace"),
        ("ambos", "Correo y WhatsApp"),
        ("descargar", "Sólo descargar"),
    ], string="Canal", required=True, default="correo")

    destinatario = fields.Selection([
        ("contacto_pago", "Contacto de compra del cliente"),
        ("encargado", "Encargado del cliente"),
        ("cliente", "Correo principal del cliente"),
        ("vendedor", "Vendedor asignado"),
        ("gerencia", "Gerencia"),
        ("manual", "Correo escrito a mano"),
    ], string="Enviar a", required=True, default="contacto_pago")

    correo_manual = fields.Char(string="Correo")
    asunto = fields.Char(string="Asunto")
    mensaje = fields.Html(string="Mensaje")
    adjuntar_pdf = fields.Boolean(string="Adjuntar PDF", default=True)

    # Diagnóstico de alcance: sin contactos no hay distribución, y es mejor
    # verlo antes de darle a enviar que descubrirlo en el log de fallos.
    sin_destino_count = fields.Integer(compute="_compute_resumen")
    sin_destino_nombres = fields.Text(compute="_compute_resumen")
    aviso_interno = fields.Boolean(compute="_compute_resumen")

    @api.model
    def default_get(self, campos):
        """Toma los clientes seleccionados en la lista o la ficha.

        Consolida a matriz: enviar el estado de cuenta a una sucursal cuando
        el saldo vive en la matriz produce un documento que no cuadra con lo
        que el cliente tiene en su contabilidad.
        """
        vals = super().default_get(campos)
        if not vals.get("partner_ids") and self.env.context.get("active_model") == "res.partner":
            ids = self.env.context.get("active_ids") or []
            partners = self.env["res.partner"].browse(ids).mapped("commercial_partner_id")
            vals["partner_ids"] = [(6, 0, partners.ids)]
        return vals

    @api.depends("partner_ids", "tipo", "destinatario", "correo_manual", "canal")
    def _compute_resumen(self):
        for w in self:
            w.partner_count = len(w.partner_ids)
            # Lo que falta depende del canal: por correo hace falta el correo,
            # por WhatsApp hace falta el número. Avisar de un correo vacío
            # cuando el envío va por WhatsApp confunde y hace pensar que el
            # cliente quedará fuera cuando sí tiene teléfono.
            if w.canal == "descargar":
                faltan = w.partner_ids.browse()
            elif w.canal == "whatsapp":
                faltan = w.partner_ids.filtered(lambda p: not w._whatsapp_de(p))
            elif w.canal == "ambos":
                faltan = w.partner_ids.filtered(
                    lambda p: not w._destino_de(p) or not w._whatsapp_de(p))
            else:
                faltan = w.partner_ids.filtered(lambda p: not w._destino_de(p))
            w.sin_destino_count = len(faltan)
            w.sin_destino_nombres = ", ".join(faltan.mapped("display_name")[:15])
            w.aviso_interno = w.tipo in ("ficha", "cartera") and w.destinatario in (
                "contacto_pago", "encargado", "cliente")

    # ------------------------------------------------------------------
    def _destino_de(self, partner):
        """Correo del destinatario según la elección, o False."""
        self.ensure_one()
        if self.destinatario == "manual":
            return self.correo_manual or False
        # Mismo criterio que el cron «Cobro automático CxC»: un cliente marcado
        # para excluir de recordatorios automáticos no recibe correo del
        # módulo tampoco. Los reportes internos no le aplican.
        if self.tipo == "estado_cuenta" and self.destinatario in (
                "contacto_pago", "encargado", "cliente"):
            if getattr(partner, "x_studio_excluir_recordatorios_automticos_1", False):
                return False
        if self.destinatario == "contacto_pago":
            return partner.ag_email_contacto_compra or partner.email or False
        if self.destinatario == "encargado":
            return partner.ag_email_encargado or partner.email or False
        if self.destinatario == "cliente":
            return partner.email or False
        if self.destinatario == "vendedor":
            return partner.user_id.email or False
        if self.destinatario == "gerencia":
            return self.env.company.email or self.env.user.email or False
        return False

    def _whatsapp_de(self, partner):
        self.ensure_one()
        if self.destinatario == "encargado":
            crudo = partner.ag_whatsapp_encargado
        else:
            crudo = partner.ag_whatsapp_contacto_compra
        crudo = crudo or partner.mobile or partner.phone
        if not crudo:
            return False
        digitos = "".join(ch for ch in crudo if ch.isdigit())
        if len(digitos) == 10:          # número dominicano sin país
            digitos = "1" + digitos
        return digitos or False

    # ------------------------------------------------------------------
    def _plantilla(self):
        return {
            "estado_cuenta": "ag_desempeno_clientes.report_ag_estado_cuenta",
            "ficha": "ag_desempeno_clientes.report_ag_ficha_cliente",
            "cartera": "ag_desempeno_clientes.report_ag_cartera",
        }[self.tipo]

    def _asunto_por_defecto(self, partner):
        hoy = fields.Date.context_today(self).strftime("%d/%m/%Y")
        if self.tipo == "estado_cuenta":
            return _("Estado de cuenta %s — al %s") % (partner.name, hoy)
        if self.tipo == "ficha":
            return _("Desempeño de %s — al %s") % (partner.name, hoy)
        return _("Cartera de clientes — al %s") % hoy

    def _vendedor_de(self, partner):
        """Quién firma: el vendedor asignado, no quien aprieta el botón.

        El cliente responde a una persona que conoce. Si Martín dispara el
        envío pero el cliente es de Gabriel, el mensaje tiene que decir
        Gabriel — si no, el cliente contesta a alguien que no le lleva la
        cuenta y la respuesta se pierde.
        """
        return partner.user_id or self.env.user

    def _texto_whatsapp(self, partner):
        saldo = "RD$ {:,.2f}".format(partner.ag_cxc_total or 0.0)
        nombre = (partner.ag_nombre_contacto_compra
                  or partner.ag_nombre_encargado or partner.name)
        vendedor = self._vendedor_de(partner)
        tel = self.env.company.phone or "(809) 612-2020"
        if self.tipo == "estado_cuenta":
            saludo = _(
                "Buenos días, %(nombre)s. Le escribe %(vendedor)s, su vendedor "
                "en %(compania)s.") % {
                    "nombre": nombre, "vendedor": vendedor.name,
                    "compania": self.env.company.ag_nombre_publico}
            cuerpo = _(
                "Le comparto el estado de cuenta de %(cliente)s: saldo de "
                "%(saldo)s.") % {"cliente": partner.name, "saldo": saldo}
            if partner.ag_cxc_vencido:
                cuerpo += _(" De ese total, RD$ {:,.2f} están vencidos.").format(
                    partner.ag_cxc_vencido)
            enlace = partner.ag_url_estado_cuenta()
            return "%s\n\n%s\n\n%s\n\n%s\n— %s · %s · %s" % (
                saludo, cuerpo, enlace,
                _("Cualquier duda quedo a la orden."),
                vendedor.name, self.env.company.ag_nombre_publico, tel)
        return _("%(cliente)s — reporte disponible.\n\n— %(firma)s") % {
            "cliente": partner.name, "firma": vendedor.name}

    # ------------------------------------------------------------------
    def action_enviar(self):
        self.ensure_one()
        if self.aviso_interno:
            raise UserError(_(
                "La ficha de desempeño y la cartera son documentos internos: "
                "llevan el grado del cliente y su posición frente a otros. "
                "Al cliente sólo se le envía el estado de cuenta."))

        if self.canal == "descargar":
            return self.action_descargar()

        plantilla = self._plantilla()
        enviados, omitidos = 0, []

        for partner in self.partner_ids:
            correo = self._destino_de(partner)
            if self.canal in ("correo", "ambos"):
                if not correo:
                    omitidos.append(partner.display_name)
                    continue
                adjuntos = []
                if self.adjuntar_pdf:
                    pdf, _fmt = self.env["ir.actions.report"]._render_qweb_pdf(
                        plantilla, res_ids=partner.ids)
                    nombre = "%s_%s.pdf" % (
                        self.tipo,
                        (partner.name or "cliente").replace(" ", "_")[:40])
                    adjuntos.append(self.env["ir.attachment"].create({
                        "name": nombre,
                        "datas": base64.b64encode(pdf),
                        "res_model": "res.partner",
                        "res_id": partner.id,
                        "mimetype": "application/pdf",
                    }).id)

                cuerpo = self.mensaje or self._cuerpo_correo(partner)
                # El correo lo firma el vendedor asignado, así que la respuesta
                # tiene que llegarle a él y no a quien apretó el botón.
                vendedor = self._vendedor_de(partner)
                self.env["mail.mail"].sudo().create({
                    "subject": self.asunto or self._asunto_por_defecto(partner),
                    "body_html": cuerpo,
                    "email_to": correo,
                    "email_from": (self.env.user.email_formatted
                                   or self.env.company.email),
                    "reply_to": (vendedor.email_formatted
                                 or self.env.user.email_formatted
                                 or self.env.company.email),
                    "attachment_ids": [(6, 0, adjuntos)],
                    "auto_delete": False,
                }).send()
                partner.message_post(
                    body=_("Reporte «%s» enviado a %s.") % (
                        dict(self._fields["tipo"].selection)[self.tipo], correo))
                enviados += 1

        if self.canal in ("whatsapp", "ambos"):
            return self._abrir_whatsapp()

        mensaje = _("%s envío(s) realizados.") % enviados
        if omitidos:
            mensaje += _(" Sin correo registrado: %s.") % ", ".join(omitidos[:10])
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Reportes enviados"),
                "message": mensaje,
                "type": "success" if not omitidos else "warning",
                "sticky": bool(omitidos),
                # Sin esto el diálogo se queda abierto después de enviar y el
                # usuario vuelve a apretar Enviar: dos correos al mismo cliente.
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _cuerpo_correo(self, partner):
        nombre = (partner.ag_nombre_contacto_compra
                  or partner.ag_nombre_encargado or partner.name)
        vendedor = self._vendedor_de(partner)
        if self.tipo == "estado_cuenta":
            filas = ""
            for f in partner.ag_facturas_abiertas():
                vencida = f.invoice_date_due and f.invoice_date_due < fields.Date.context_today(self)
                filas += (
                    "<tr>"
                    "<td style='padding:6px 10px;border-bottom:1px solid #eee'>%s</td>"
                    "<td style='padding:6px 10px;border-bottom:1px solid #eee'>%s%s</td>"
                    "<td style='padding:6px 10px;border-bottom:1px solid #eee;"
                    "text-align:right'>RD$ %s</td></tr>"
                ) % (
                    f.name,
                    f.invoice_date_due and f.invoice_date_due.strftime("%d/%m/%Y") or "—",
                    " <span style='color:#d03b3b'>(vencida)</span>" if vencida else "",
                    "{:,.2f}".format(f.amount_residual),
                )
            hoy = fields.Date.context_today(self)
            return """
<div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;
            border:1px solid #e0e0e0;border-radius:6px;overflow:hidden">
  %(header)s
  <div style="padding:25px 30px">
    <p style="color:#333;margin-top:0">Apreciado(a) <b>%(nombre)s</b>,</p>
    <p style="color:#555">Le escribe <b>%(vendedor)s</b>, su vendedor en
       %(compania)s. Le compartimos el estado de cuenta de
       <b>%(cliente)s</b> al %(fecha)s.</p>
    <div style="text-align:center;padding:16px;background:#f4eef8;border-radius:6px;margin:16px 0">
      <div style="font-size:11px;color:#6c3883;text-transform:uppercase;letter-spacing:.08em">Saldo pendiente</div>
      <div style="font-size:26px;font-weight:bold;color:#333;margin-top:4px">RD$ %(saldo)s</div>
      %(vencido)s
    </div>
    <table style="width:100%%;border-collapse:collapse;font-size:12px">
      <thead><tr style="background-color:#6c3883;color:white">
        <th style="padding:8px;text-align:left">Factura</th>
        <th style="padding:8px;text-align:left">Vence</th>
        <th style="padding:8px;text-align:right">Saldo</th>
      </tr></thead>
      <tbody>%(filas)s</tbody>
    </table>
    <p style="color:#555;margin-top:18px;font-size:13px">El detalle completo
       está en el PDF adjunto. Para consultas o acuerdos de pago comuníquese
       con nosotros al <b>(809) 612-2020</b>.</p>
    <p style="color:#333">%(firma)s<br>
       <span style="color:#888;font-size:12px">Su vendedor en %(compania)s</span></p>
  </div>
  %(footer)s
</div>""" % {
                "header": self._encabezado_ag(
                    "Estado de Cuenta", hoy.strftime("%d/%m/%Y")),
                "footer": self._pie_ag(responder=True),
                "nombre": nombre,
                "cliente": partner.name,
                "fecha": hoy.strftime("%d/%m/%Y"),
                "saldo": "{:,.2f}".format(partner.ag_cxc_total or 0.0),
                "vencido": ('<div style="color:#c00;font-size:13px;margin-top:6px">'
                            'RD$ %s vencidos</div>'
                            % "{:,.2f}".format(partner.ag_cxc_vencido)
                            if partner.ag_cxc_vencido else ""),
                "filas": filas,
                "firma": vendedor.name,
                "vendedor": vendedor.name,
                "compania": self.env.company.ag_nombre_publico,
            }
        return """
<div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;
            border:1px solid #e0e0e0;border-radius:6px;overflow:hidden">
  %(header)s
  <div style="padding:25px 30px">
    <p style="color:#333;margin-top:0">Adjunto el reporte de desempeño de
       <b>%(cliente)s</b> al %(fecha)s.</p>
    <p style="color:#888;font-size:12px">Documento interno: contiene el grado
       del cliente y su posición frente a otros. No reenviar al cliente.</p>
    <p style="color:#333">%(firma)s</p>
  </div>
  %(footer)s
</div>""" % {
            "header": self._encabezado_ag(
                "Desempeño de Cliente",
                fields.Date.context_today(self).strftime("%d/%m/%Y")),
            "footer": self._pie_ag(),
            "cliente": partner.name,
            "fecha": fields.Date.context_today(self).strftime("%d/%m/%Y"),
            "firma": self.env.user.name,
        }

    def _abrir_whatsapp(self):
        """Abre WhatsApp con el mensaje redactado y el enlace ya dentro.

        Sin el módulo `whatsapp` de Enterprise ni la API de Meta, ésta es la
        vía inmediata: el vendedor dispara el envío desde su propio número,
        que además es el que el cliente reconoce.
        """
        self.ensure_one()
        partner = self.partner_ids[0]
        numero = self._whatsapp_de(partner)
        if not numero:
            raise UserError(_(
                "%s no tiene WhatsApp ni teléfono registrado. Complete "
                "«WhatsApp» en la pestaña de contactos AG de la ficha.")
                % partner.display_name)
        texto = quote(self._texto_whatsapp(partner))
        partner.message_post(body=_("Estado de cuenta enviado por WhatsApp al %s.") % numero)
        return {
            "type": "ir.actions.act_url",
            "url": "https://wa.me/%s?text=%s" % (numero, texto),
            "target": "new",
        }

    def action_descargar(self):
        self.ensure_one()
        return self.env.ref(
            "%s_action" % self._plantilla()
        ).report_action(self.partner_ids.ids)

    def action_previsualizar(self):
        """Abre el HTML del reporte — el mismo que alimenta el PDF."""
        self.ensure_one()
        return self.env.ref(
            "%s_action" % self._plantilla()
        ).with_context(report_type="html").report_action(self.partner_ids.ids)
