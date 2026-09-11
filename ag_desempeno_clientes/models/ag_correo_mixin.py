# -*- coding: utf-8 -*-
from odoo import models


class AgCorreoMixin(models.AbstractModel):
    """Identidad visual de los correos que salen de AG Supply.

    El morado #6c3883, el logo de la compañía y el pie con la dirección son
    los mismos que ya usan los crones de cobranza y los reportes semanales:
    el cliente y el vendedor deben reconocer el correo como de la casa, no
    como uno más de un sistema distinto.

    Vive aparte porque lo usan dos asistentes distintos —el de reportes de
    desempeño y el de fichas técnicas— y el encabezado tiene que ser el mismo
    en los dos. Duplicarlo garantizaba que un día se separaran.
    """
    _name = "ag.correo.mixin"
    _description = "Identidad visual de los correos de AG Supply"

    def _encabezado_ag(self, titulo, subtitulo):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        return """
    <div style="background-color:#6c3883;padding:20px 30px">
      <img src="%s/web/image/res.company/%s/logo"
           style="height:50px;vertical-align:middle;margin-right:20px" alt="AG Supply"/>
      <div style="display:inline-block;vertical-align:middle">
        <div style="color:white;font-size:18px;font-weight:bold">%s</div>
        <div style="color:#e0c8f0;font-size:12px">%s</div>
      </div>
    </div>""" % (base.rstrip("/"), self.env.company.id, titulo, subtitulo)

    def _pie_ag(self, responder=False):
        cia = self.env.company
        cierre = ("Puede responder a este correo." if responder
                  else "Reporte generado autom&aacute;ticamente &mdash; No responder a este correo")
        return """
    <div style="background-color:#f4eef8;border-top:3px solid #6c3883;padding:15px 30px;text-align:center">
      <p style="color:#6c3883;font-weight:bold;margin:0 0 4px 0;font-size:13px">%s</p>
      <p style="color:#888;font-size:11px;margin:0">%s</p>
      <p style="color:#aaa;font-size:10px;margin:6px 0 0 0">%s</p>
    </div>""" % (
            # El nombre público, no el de la compañía: el sufijo entre
            # paréntesis distingue compañías dentro de Odoo y al cliente le
            # sobra.
            cia.ag_nombre_publico or cia.name or "AG Supply, SRL.",
            "Calle Las Palomas #60, Santiago, Rep&uacute;blica Dominicana &nbsp;|&nbsp; (809) 612-2020",
            cierre)
