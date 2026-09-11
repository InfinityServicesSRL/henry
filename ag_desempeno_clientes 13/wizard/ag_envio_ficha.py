# -*- coding: utf-8 -*-
"""Envío de fichas técnicas a clientes y contactos.

Mismo canal y misma identidad visual que el envío de reportes: el cliente
recibe un correo que reconoce. Lo que cambia es el contenido —producto en vez
de cartera— y que aquí no hay nada confidencial: la ficha es material que la
empresa quiere que circule.
"""
import base64
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AgEnvioFicha(models.TransientModel):
    _name = "ag.envio.ficha"
    _inherit = "ag.correo.mixin"
    _description = "Enviar ficha técnica"

    product_ids = fields.Many2many(
        "product.template", string="Productos", required=True)
    # No es obligatorio en el modelo: para «Sólo descargar» no hace falta
    # elegir cliente. La vista lo exige cuando el canal sí lo necesita.
    partner_id = fields.Many2one(
        "res.partner", string="Cliente",
        domain="[('customer_rank', '>', 0)]")
    destinatario = fields.Selection(
        [("contacto_compra", "Contacto de compra del cliente"),
         ("encargado", "Encargado del cliente"),
         ("cliente", "Correo principal del cliente"),
         ("manual", "Correo escrito a mano")],
        string="Enviar a", default="contacto_compra", required=True)
    correo_manual = fields.Char(string="Correo")
    canal = fields.Selection(
        [("correo", "Correo con PDF adjunto"),
         ("whatsapp", "WhatsApp con enlace"),
         ("descargar", "Sólo descargar")],
        string="Canal", default="correo", required=True)
    asunto = fields.Char(placeholder="Se genera solo si lo deja vacío")
    mensaje = fields.Text(placeholder="Se genera solo si lo deja vacío")

    sin_destino = fields.Boolean(compute="_compute_resumen")
    aviso_incompletas = fields.Char(compute="_compute_resumen")
    bloqueadas = fields.Char(compute="_compute_resumen")

    # ------------------------------------------------------------------
    @api.model
    def default_get(self, campos):
        vals = super().default_get(campos)
        if not vals.get("product_ids") and self.env.context.get("active_model") == "product.template":
            vals["product_ids"] = [(6, 0, self.env.context.get("active_ids") or [])]
        return vals

    @api.depends("product_ids", "partner_id", "destinatario", "correo_manual", "canal")
    def _compute_resumen(self):
        for w in self:
            # Sin cliente elegido todavía no falta nada: el aviso sólo tiene
            # sentido una vez que se sabe a quién se le va a mandar.
            if w.canal == "descargar" or not w.partner_id:
                w.sin_destino = False
            elif w.canal == "whatsapp":
                w.sin_destino = not w._whatsapp_destino()
            else:
                w.sin_destino = not w._correo_destino()

            # Sin núcleo no hay ficha: el producto se queda fuera del envío.
            fuera = w.product_ids.filtered(
                lambda p: p.ag_ficha_estado == "sin_datos")
            w.bloqueadas = ", ".join(fuera.mapped("display_name")[:8])
            parciales = w.product_ids.filtered(
                lambda p: p.ag_ficha_estado == "parcial")
            w.aviso_incompletas = ", ".join(parciales.mapped("display_name")[:8])

    # ------------------------------------------------------------------
    def _correo_destino(self):
        self.ensure_one()
        p = self.partner_id
        if self.destinatario == "manual":
            return self.correo_manual or False
        if self.destinatario == "contacto_compra":
            return p.ag_email_contacto_compra or p.email or False
        if self.destinatario == "encargado":
            return p.ag_email_encargado or p.email or False
        return p.email or False

    def _whatsapp_destino(self):
        self.ensure_one()
        p = self.partner_id
        if self.destinatario == "encargado":
            crudo = p.ag_whatsapp_encargado
        else:
            crudo = p.ag_whatsapp_contacto_compra
        crudo = crudo or p.mobile or p.phone
        if not crudo:
            return False
        digitos = "".join(c for c in crudo if c.isdigit())
        if len(digitos) == 10:          # dominicano sin código de país
            digitos = "1" + digitos
        return digitos or False

    def _enviables(self):
        """Productos con núcleo suficiente para generar una ficha."""
        self.ensure_one()
        return self.product_ids.filtered(
            lambda p: p.ag_ficha_estado != "sin_datos")

    # ------------------------------------------------------------------
    def action_previsualizar(self):
        self.ensure_one()
        productos = self._enviables()
        if not productos:
            raise UserError(_(
                "Ninguno de los productos seleccionados tiene la ficha "
                "mínima completa. Falta: %s.") % (
                    self.product_ids[:1].ag_ficha_faltantes or "—"))
        # La versión del cliente a propósito: previsualizar sirve para ver
        # exactamente lo que va a recibir, sin el bloque de faltantes.
        return self.env.ref(
            "ag_desempeno_clientes.report_ag_ficha_tecnica_cliente_action"
        ).report_action(productos)

    def action_enviar(self):
        self.ensure_one()
        productos = self._enviables()
        if not productos:
            raise UserError(_(
                "Ninguno de los productos seleccionados tiene la ficha mínima "
                "completa, así que no hay nada que enviar."))

        if self.canal == "descargar":
            return self.action_previsualizar()
        if self.canal == "whatsapp":
            return self._abrir_whatsapp(productos)

        correo = self._correo_destino()
        if not correo:
            raise UserError(_(
                "%s no tiene correo registrado para «%s». Complételo en la "
                "ficha del cliente o escriba uno a mano.") % (
                    self.partner_id.display_name,
                    dict(self._fields["destinatario"].selection)[self.destinatario]))

        pdf, _fmt = self.env["ir.actions.report"]._render_qweb_pdf(
            "ag_desempeno_clientes.report_ag_ficha_tecnica",
            res_ids=productos.ids)
        nombre = ("ficha_tecnica.pdf" if len(productos) > 1 else
                  "ficha_%s.pdf" % (productos.name or "").replace(" ", "_")[:40])
        adjunto = self.env["ir.attachment"].create({
            "name": nombre,
            "datas": base64.b64encode(pdf),
            "res_model": "product.template",
            "res_id": productos[0].id,
            "mimetype": "application/pdf",
        })

        vendedor = self.partner_id.user_id or self.env.user
        self.env["mail.mail"].sudo().create({
            "subject": self.asunto or self._asunto(productos),
            "body_html": self.mensaje or self._cuerpo(productos, vendedor),
            "email_to": correo,
            "email_from": self.env.user.email_formatted or self.env.company.email,
            "reply_to": (vendedor.email_formatted
                         or self.env.user.email_formatted
                         or self.env.company.email),
            "attachment_ids": [(6, 0, adjunto.ids)],
            "auto_delete": False,
        }).send()
        self.partner_id.message_post(body=_(
            "Ficha técnica de %s enviada a %s.") % (
                ", ".join(productos.mapped("name")[:5]), correo))

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Ficha enviada"),
                "message": _("%s ficha(s) enviadas a %s.") % (len(productos), correo),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _abrir_whatsapp(self, productos):
        self.ensure_one()
        numero = self._whatsapp_destino()
        if not numero:
            raise UserError(_(
                "%s no tiene WhatsApp ni teléfono registrado.")
                % self.partner_id.display_name)
        base = (self.env["ir.config_parameter"].sudo()
                .get_param("web.base.url") or "").rstrip("/")
        lineas = [
            _("Buenos días, %(nombre)s. Le escribe %(vendedor)s de %(compania)s.") % {
                "nombre": (self.partner_id.ag_nombre_contacto_compra
                           or self.partner_id.name),
                "vendedor": (self.partner_id.user_id or self.env.user).name,
                "compania": self.env.company.ag_nombre_publico},
            "",
            _("Le comparto la ficha técnica de:"),
        ]
        for p in productos[:5]:
            lineas.append("• %s — %s/ag/ficha/%s" % (p.name, base, p.id))
        lineas += ["", _("Cualquier duda quedo a la orden.")]
        self.partner_id.message_post(body=_(
            "Ficha técnica compartida por WhatsApp al %s.") % numero)
        return {
            "type": "ir.actions.act_url",
            "url": "https://wa.me/%s?text=%s" % (numero, quote("\n".join(lineas))),
            "target": "new",
        }

    # ------------------------------------------------------------------
    def _asunto(self, productos):
        if len(productos) == 1:
            return _("Ficha técnica — %s") % productos.name
        return _("Fichas técnicas — %s productos") % len(productos)

    def _cuerpo(self, productos, vendedor):
        nombre = (self.partner_id.ag_nombre_contacto_compra
                  or self.partner_id.ag_nombre_encargado
                  or self.partner_id.name)
        filas = ""
        for p in productos:
            detalle = " · ".join(x for x in [
                p.ag_tipo_de_papel,
                "%s capas" % p.ag_n_de_capas if p.ag_n_de_capas else "",
                "%g g/m²" % p.ag_gramaje if p.ag_gramaje else "",
            ] if x)
            filas += (
                "<tr><td style='padding:8px 10px;border-bottom:1px solid #eee'>"
                "<b>%s</b><div style='color:#777;font-size:12px'>%s</div></td></tr>"
            ) % (p.name, detalle)
        return """
<div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;
            border:1px solid #e0e0e0;border-radius:6px;overflow:hidden">
  %(header)s
  <div style="padding:25px 30px">
    <p style="color:#333;margin-top:0">Apreciado(a) <b>%(nombre)s</b>,</p>
    <p style="color:#555">Le escribe <b>%(vendedor)s</b>, su vendedor en
       %(compania)s. Le comparto la ficha t&eacute;cnica de:</p>
    <table style="width:100%%;border-collapse:collapse;margin:15px 0">%(filas)s</table>
    <p style="color:#555">El detalle completo va adjunto en PDF. Quedo a la
       orden para cualquier especificaci&oacute;n adicional.</p>
  </div>
  %(footer)s
</div>""" % {
            "header": self._encabezado_ag(
                "Ficha técnica", self.env.company.ag_nombre_publico or ""),
            "nombre": nombre,
            "vendedor": vendedor.name,
            "compania": self.env.company.ag_nombre_publico,
            "filas": filas,
            "footer": self._pie_ag(responder=True),
        }
