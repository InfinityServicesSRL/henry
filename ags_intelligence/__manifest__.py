# -*- coding: utf-8 -*-
{
    "name": "AG Intelligence",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Analisis financiero, proyeccion de demanda e inteligencia comercial para AG Supply",
    "description": """
AG Intelligence
===============
Modulo de analitica para AG Supply. Convierte los datos de Odoo (ventas,
inventario, fabricacion, compras, contabilidad y nomina) en decisiones de
compra, costo y margen.

Motores:
  - Motor A: demanda y abastecimiento (pronostico -> materiales -> compras).
  - Motor B: analisis financiero (costos, variaciones, P&L, presupuesto, caja).
  - Inteligencia comercial (whitespace, RFM, venta cruzada, voz del cliente).
  - Capa de IA (supuestos con aprobacion, insights, auditoria).

Fase 1 (andamiaje): estructura instalable, seguridad y menu raiz.
""",
    "author": "AG Supply, SRL.",
    "website": "https://agsupply.com.do",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "account",
        "sale_management",
        "stock",
        "mrp",
        "purchase",
        "hr",
    ],
    "data": [
        "security/ags_security.xml",
        "security/ir.model.access.csv",
        "views/ags_welcome_views.xml",
        "views/ags_menus.xml",
    ],
    "application": True,
    "installable": True,
}
