# -*- coding: utf-8 -*-
{
    "name": "AG Supply - Desempeño de Clientes",
    "version": "18.0.1.0.0",
    "summary": "Evaluación cuantitativa de clientes: score, tendencia, alertas y envío de reportes",
    "description": """
Desempeño de Clientes — AG Supply
=================================
Cuatro capas:

* **Ficha** — estado actual del cliente: pipeline, backlog, crédito disponible,
  cuentas por cobrar, historial de compras y entregas.
* **Score** — evaluación 0-100 normalizada por percentil dentro del segmento
  del cliente (``ag_segmento_categoria``), en ventana de 12 meses móviles.
* **Alertas** — condiciones accionables independientes del score.
* **Distribución** — reportes HTML/PDF enviables por correo y WhatsApp a
  contactos del cliente y a vendedores, sin requerir acceso a Odoo.

Consolida por matriz (``commercial_partner_id``): las sucursales suman al
cliente comercial.
    """,
    "author": "AG Supply, SRL.",
    "website": "https://agsupply.com.do",
    "category": "Sales/Sales",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "sale_management",
        "account",
        "stock",
        "portal",
        # Define res.partner.route y todos los campos ag_* del contacto.
        # Sin esta dependencia el registro de Odoo falla al reconstruirse.
        "module_agsupply",
    ],
    "data": [
        "security/ag_desempeno_security.xml",
        "security/ir.model.access.csv",
        # Los reportes van antes que las plantillas de correo y las vistas:
        # ambas los referencian por xml_id y Odoo resuelve en orden de carga.
        "report/report_layout.xml",
        "report/report_ficha_cliente.xml",
        "report/report_cartera.xml",
        "report/report_estado_cuenta.xml",
        "report/report_ficha_tecnica.xml",
        "report/report_auditoria_fichas.xml",
        "report/report_actions.xml",
        "data/ag_desempeno_config_data.xml",
        "data/mail_template_data.xml",
        "data/ir_cron_data.xml",
        # Va después de report_actions.xml: renombra el consolidado viejo de
        # infinity_statement para que no se confunda con el nuestro.
        "data/ag_renombrar_reportes.xml",
        "views/ag_desempeno_config_views.xml",
        "views/ag_desempeno_snapshot_views.xml",
        "views/ag_desempeno_alerta_views.xml",
        "views/res_partner_views.xml",
        "views/product_template_views.xml",
        "views/ag_desempeno_tablero_views.xml",
        "wizard/ag_envio_reporte_views.xml",
        "wizard/ag_envio_ficha_views.xml",
        "views/ag_desempeno_menus.xml",
        "views/res_partner_route_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "ag_desempeno_clientes/static/src/scss/ag_desempeno.scss",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
