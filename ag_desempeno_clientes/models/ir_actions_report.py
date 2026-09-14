# -*- coding: utf-8 -*-
from odoo import api, models

# El consolidado viejo vive en infinity_statement y se llamaba "Customer
# Statement" / "Estado de Cuenta Cliente". Junto al nuestro, en el mismo menú
# Imprimir, los dos nombres eran casi iguales y nadie sabía cuál abrir.
# El nuestro se queda con "Estado de cuenta"; el viejo pasa a "Consolidado
# del cliente".
REPORTE_VIEJO = "infinity_statement.report_customer_statement"
NOMBRE_NUEVO = "Consolidado del cliente"


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    @api.model
    def _ag_renombrar_consolidado(self):
        """Renombra el reporte de infinity_statement, si está instalado.

        Se llama desde data/ag_renombrar_reportes.xml, que corre en cada
        instalación y en cada actualización de este módulo: así el nombre
        vuelve a quedar puesto si una actualización de infinity_statement lo
        revierte.

        Va como método y no como <record> a propósito: referenciar el xml_id
        desde XML obligaría a declarar infinity_statement en depends, y este
        módulo tiene que poder instalarse sin él.
        """
        reporte = self.env.ref(REPORTE_VIEJO, raise_if_not_found=False)
        if not reporte:
            return
        # `name` es un campo traducible: hay que escribirlo en cada idioma
        # activo o el usuario en español sigue viendo la traducción vieja.
        idiomas = self.env["res.lang"].search([]).mapped("code") or ["en_US"]
        for codigo in idiomas:
            reporte.with_context(lang=codigo).name = NOMBRE_NUEVO
