# -*- coding: utf-8 -*-
from odoo import models, fields


class AgsWelcome(models.Model):
    """Modelo de inicio del modulo (Fase 1 - andamiaje).

    Sirve como pantalla de aterrizaje mientras se construyen los tableros
    de las fases siguientes. Se reemplaza/extiende en la Fase 2 (fundacion).
    """
    _name = "ags.welcome"
    _description = "AG Intelligence - Inicio"

    name = fields.Char(string="Nombre", default="AG Intelligence")
    nota = fields.Text(
        string="Nota",
        default="Modulo instalado correctamente (Fase 1 - andamiaje). "
        "Los tableros se construyen en las fases siguientes.",
    )
