# -*- coding: utf-8 -*-
"""Clasificacion inicial de indicadores confidenciales.

Marca las secciones que exponen margen, costo unitario, P&L y cuentas por
pagar. Es un punto de partida editable desde la interfaz, no una regla fija:
por eso corre una sola vez y no vuelve a pisar lo que alguien ajuste despues.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

SECCIONES_SENSIBLES = ("costos", "financiero")


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    parametros = env["ags.parametro"].with_context(active_test=False).search([
        ("seccion", "in", list(SECCIONES_SENSIBLES)),
        ("confidencial", "=", False),
    ])
    parametros.write({"confidencial": True})
    _logger.info("Etapa 7: %s parametros marcados como confidenciales",
                 len(parametros))
