# -*- coding: utf-8 -*-
{
    "name": "AG Intelligence",
    "version": "18.0.3.0.0",
    "category": "Productivity",
    "summary": "Analisis financiero, proyeccion de demanda e inteligencia comercial para AG Supply",
    "description": """
AG Intelligence
===============
Modulo de analitica para AG Supply. Convierte los datos de Odoo (ventas,
inventario, fabricacion, compras, contabilidad y nomina) en decisiones de
compra, costo y margen.

Los modulos de Odoo registran el pasado. AG Intelligence responde tres
preguntas sobre esos mismos datos:
  - Diagnostico: que paso y por que
  - Control: estamos dentro de lo planeado
  - Prediccion: que va a pasar y que hacer al respecto

Motores:
  - Motor A: demanda y abastecimiento (pronostico -> materiales -> compras).
  - Motor B: analisis financiero (costos, variaciones, P&L, presupuesto, caja).
  - Inteligencia comercial (whitespace, RFM, venta cruzada, voz del cliente).
  - Capa de IA (supuestos con aprobacion, insights, auditoria).

Fase 1 (completada): estructura instalable, seguridad y menu raiz.

Fase 2A (esta version): sistema de parametros de gestion.
  - Catalogo de parametros medibles por seccion
  - Baselines congelados e inmutables (el punto de partida real)
  - Benchmarks de mercado versionados, con fuente y ajuste documentado
  - Mediciones con doble comparacion: contra baseline y contra mercado
  - Factores de estacionalidad y proyecciones auditables
  - Calculadores de Salud del ERP

El sistema distingue tres calidades de referencia externa:
  A - Dato duro dominicano verificable (ej. tasa de interes del sector)
  B - Rango de industria global adaptado (ej. OEE, merma de conversion)
  C - Sin referencia publica confiable: se compara contra el historico propio

Los parametros tipo C se cargan deliberadamente SIN benchmark. Un vacio
honesto es preferible a un numero inventado cuando estos valores se congelan
como referencia en un sistema de gestion real.
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
        # Seguridad primero: los grupos deben existir antes que los accesos
        "security/ags_security.xml",
        "security/ir.model.access.csv",
        # Vistas y acciones antes que los menus que las referencian
        "views/ags_welcome_views.xml",
        "views/ags_parametro_views.xml",
        "views/ags_valores_views.xml",
        "views/ags_config_views.xml",
        "views/ags_aging_views.xml",
        "views/ags_menus.xml",
        # Datos semilla
        "data/ags_fuentes_data.xml",
        "data/ags_parametros_data.xml",
        "data/ags_parametros_2b_data.xml",
        "data/ags_parametros_2c_data.xml",
        "data/ags_parametros_2d_data.xml",
        "data/ags_parametros_cartera_data.xml",
        "data/ags_parametros_cxp_data.xml",
        "data/ags_regimenes_data.xml",
        "data/ags_benchmarks_data.xml",
        "data/ags_cron.xml",
        "data/ags_cron_aging.xml",
    ],
    "application": True,
    "installable": True,
}
