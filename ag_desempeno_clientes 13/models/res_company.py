# -*- coding: utf-8 -*-
import re

from odoo import api, fields, models

# "AG Supply S.R.L (STI)" → "AG Supply S.R.L". El sufijo entre paréntesis
# distingue compañías dentro de Odoo; para el cliente es ruido.
SUFIJO_INTERNO = re.compile(r"\s*\([^)]*\)\s*$")


class ResCompany(models.Model):
    _inherit = "res.company"

    ag_nombre_publico = fields.Char(
        string="Nombre para clientes",
        compute="_compute_ag_nombre_publico",
        store=True,
        readonly=False,
        help="Cómo aparece la empresa en lo que ve el cliente: estado de "
             "cuenta, correo y mensaje de WhatsApp. Si se deja vacío se usa "
             "el nombre de la compañía sin el sufijo entre paréntesis, de "
             "modo que «AG Supply S.R.L (STI)» sale como «AG Supply S.R.L».")

    @api.depends("name")
    def _compute_ag_nombre_publico(self):
        for company in self:
            # Lo escrito a mano manda: sólo se calcula cuando está vacío.
            if company.ag_nombre_publico:
                continue
            limpio = SUFIJO_INTERNO.sub("", company.name or "").strip()
            company.ag_nombre_publico = limpio or company.name
