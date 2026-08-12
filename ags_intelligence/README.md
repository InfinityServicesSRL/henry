# AG Intelligence (`ags_intelligence`)

Módulo de analítica de AG Supply para Odoo 18. Convierte los datos de Odoo en
decisiones de compra, costo y margen.

## Estado: Fase 1 — Andamiaje

Esta versión es el esqueleto instalable:

- Manifiesto con dependencias (account, sale, stock, mrp, purchase, hr).
- Grupo de seguridad **Gerencia / Contabilidad** (`group_ags_manager`).
- Menú raíz **Inteligencia** con una pantalla de **Inicio**.

Los tableros (Cockpit de Gerencia, Demanda y Ventas, Inventario y Materiales,
Costos y Rentabilidad, Financiero y Caja, Inteligencia Comercial) se construyen
en las fases siguientes.

## Instalación (staging)

1. Subir la carpeta `ags_intelligence/` al repositorio `henry`.
2. Odoo.sh reconstruye staging.
3. En Odoo: Apps → actualizar lista → instalar **AG Intelligence**.
4. Asignar el grupo **Gerencia / Contabilidad** a los usuarios autorizados.

## Estructura

```
ags_intelligence/
  __manifest__.py
  __init__.py
  models/
    __init__.py
    ags_welcome.py
  security/
    ags_security.xml
    ir.model.access.csv
  views/
    ags_welcome_views.xml
    ags_menus.xml
  README.md
```
