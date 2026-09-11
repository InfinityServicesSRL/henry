# -*- coding: utf-8 -*-
"""Página pública del estado de cuenta.

El contacto del cliente no tiene usuario de Odoo y no debería necesitarlo.
Abre un enlace con token, ve sus facturas, descarga el PDF, y nada más: el
token caduca, sólo resuelve al cliente que lo generó, y la página no expone
score, grado ni comparación con otros clientes.
"""

from odoo import fields, http
from odoo.http import request


class AgDesempenoPortal(http.Controller):

    @http.route("/ag/estado-cuenta/<int:partner_id>/<string:token>",
                type="http", auth="public", website=False, sitemap=False)
    def estado_cuenta(self, partner_id, token, **kw):
        partner = request.env["res.partner"].sudo().browse(partner_id)
        error = self._validar(partner, token)
        if error:
            return request.render("ag_desempeno_clientes.portal_enlace_invalido",
                                  {"motivo": error})
        return request.render("ag_desempeno_clientes.portal_estado_cuenta", {
            "partner": partner,
            "facturas": partner.ag_facturas_abiertas(),
            "hoy": fields.Date.context_today(partner),
            "compania": partner.company_id or request.env.company,
        })

    @http.route("/ag/estado-cuenta/<int:partner_id>/<string:token>/pdf",
                type="http", auth="public", sitemap=False)
    def estado_cuenta_pdf(self, partner_id, token, **kw):
        partner = request.env["res.partner"].sudo().browse(partner_id)
        if self._validar(partner, token):
            return request.not_found()
        pdf, _fmt = request.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "ag_desempeno_clientes.report_ag_estado_cuenta", res_ids=[partner.id])
        nombre = "estado_cuenta_%s.pdf" % (partner.name or "").replace(" ", "_")[:40]
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", 'inline; filename="%s"' % nombre),
        ])

    @staticmethod
    def _validar(partner, token):
        if not partner.exists() or not partner.ag_portal_token:
            return "no_existe"
        if partner.ag_portal_token != token:
            return "no_existe"
        if partner.ag_portal_expira and partner.ag_portal_expira < fields.Date.context_today(partner):
            return "expirado"
        return None

    # ------------------------------------------------------------------
    # Ficha técnica pública.
    #
    # Sin token, a diferencia del estado de cuenta: una ficha técnica es
    # material que la empresa quiere que circule —el gramaje está impreso en
    # el fardo— y un enlace con token no se puede reenviar, que es justo lo
    # que uno quiere que el cliente haga con ella.
    #
    # Lo que sí se controla: sólo productos vendibles y con la ficha mínima
    # completa. Nada de costo, margen, comisión ni suplidor sale nunca, porque
    # no están en la plantilla.
    # ------------------------------------------------------------------
    @http.route("/ag/ficha/<int:product_id>", type="http", auth="public",
                website=False, sitemap=False)
    def ficha_tecnica(self, product_id, **kw):
        producto = request.env["product.template"].sudo().browse(product_id)
        if not self._ficha_publicable(producto):
            return request.render("ag_desempeno_clientes.portal_ficha_no_disponible", {})
        return request.render("ag_desempeno_clientes.portal_ficha_tecnica", {
            "producto": producto,
            "compania": producto.company_id or request.env.company,
            "filas": self._filas_ficha(producto),
        })

    @http.route("/ag/ficha/<int:product_id>/pdf", type="http", auth="public",
                sitemap=False)
    def ficha_tecnica_pdf(self, product_id, **kw):
        producto = request.env["product.template"].sudo().browse(product_id)
        if not self._ficha_publicable(producto):
            return request.not_found()
        pdf, _fmt = request.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "ag_desempeno_clientes.report_ag_ficha_tecnica", res_ids=[producto.id])
        nombre = "ficha_%s.pdf" % (producto.name or "").replace(" ", "_")[:40]
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", 'inline; filename="%s"' % nombre),
        ])

    @staticmethod
    def _ficha_publicable(producto):
        return bool(
            producto.exists()
            and producto.sale_ok
            and producto.ag_ficha_estado != "sin_datos")

    @staticmethod
    def _filas_ficha(p):
        """Sólo las filas con dato: una fila que dice 0 miente."""
        medida = p._ag_medida()
        filas = [
            ("Tipo de papel", p.ag_tipo_de_papel),
            ("N° de capas", p.ag_n_de_capas or ""),
            ("Gramaje", "%g g/m²" % p.ag_gramaje if p.ag_gramaje else ""),
            # La medida sale del modelo, no de aquí: la página y el PDF tienen
            # que decir lo mismo, y la regla de hoja-o-rollo vive en un lugar.
            (medida.get("etiqueta", "Medida"), medida.get("valor", "")),
            ("Hojas por fardo",
             "{:,}".format(p.ag_cantidad_de_hojas) if p.ag_cantidad_de_hojas else ""),
            ("Doblez", p.ag_doblez),
            ("Color", p.ag_color),
            ("Acabado", p.ag_acabado),
            ("Materia prima", p.ag_materia_prima_utilizada),
            ("Unidades por fardo",
             "{:,}".format(p.ag_paquete) if p.ag_paquete else ""),
            ("Empaque primario", p.ag_empaque_primario),
            ("Empaque secundario", p.ag_empaque_secundario),
            ("Peso bruto del fardo",
             "%g kg" % p.ag_peso_bruto_caja if p.ag_peso_bruto_caja else ""),
        ]
        return [(et, str(v)) for et, v in filas if v]
