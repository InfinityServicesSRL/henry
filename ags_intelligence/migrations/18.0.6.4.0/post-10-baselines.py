# -*- coding: utf-8 -*-
"""Etapa 0 del cockpit: base de confianza.

1. Congela un baseline por parametro que tenga mediciones (idempotente).
2. Fuerza el recomputo de las comparaciones ya almacenadas, que el
   @api.depends ampliado por si solo no dispara sobre registros existentes.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    resumen = env["ags.baseline"].generar_baselines_iniciales()
    _logger.info("Etapa 0 - baselines: %s", resumen)
    total = env["ags.medicion"].recomputar_comparaciones()
    _logger.info("Etapa 0 - mediciones recomputadas: %s", total)
