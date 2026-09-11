# -*- coding: utf-8 -*-
"""Siembra de datos para el ambiente de pruebas.

NO se instala con el módulo — es un script suelto que se corre a mano en el
shell de Odoo de la rama *staging*, para que la primera vez que se abra el
módulo no se vea vacío.

Qué hace:
  1. Crea las 18 rutas geográficas propuestas, con su zona, distancia y costo
     logístico sugerido.
  2. Reasigna por ciudad los clientes que hoy no tienen ruta.
  3. Pone potencial, kilómetros, costo logístico y perfil de ejemplo en los
     diez clientes de mayor facturación, para poder ver la ficha completa.
  4. Recalcula toda la cartera.

Cómo se corre en Odoo.sh (rama staging):

    odoo-bin shell -d <base_staging>
    >>> exec(open('/home/odoo/src/user/ag_desempeno_clientes/tools/datos_prueba.py').read())

O desde el editor web de Odoo.sh, pestaña Shell.

NUNCA correrlo en producción: sobrescribe rutas y campos de clientes reales.
"""

from odoo import fields

# ---------------------------------------------------------------------------
# 1. Rutas geográficas. Las distancias son aproximadas por carretera desde la
#    planta de Santiago y hay que validarlas con quien maneja los camiones.
# ---------------------------------------------------------------------------
RUTAS = [
    # (nombre, zona, km, costo, día de visita, ciudades que caen aquí)
    ("Santiago Centro",        "santiago", 6,   "2", "1", ["SANTIAGO", "SANTIAGO DE LOS CABALLEROS"]),
    ("Santiago Norte",         "santiago", 9,   "2", "2", []),
    ("Santiago Sur",           "santiago", 9,   "2", "3", []),
    ("Santiago Este",          "santiago", 11,  "2", "4", []),
    ("Santiago Oeste",         "santiago", 11,  "2", "5", []),
    ("Tamboril/Canca",         "cibao",    12,  "2", "2", ["TAMBORIL", "CANCA"]),
    ("Navarrete",              "cibao",    20,  "2", "3", ["NAVARRETE"]),
    ("Moca/Espaillat",         "cibao",    35,  "3", "2", ["MOCA", "ESPAILLAT"]),
    ("Salcedo",                "cibao",    55,  "3", "4", ["SALCEDO"]),
    ("La Vega",                "cibao",    60,  "3", "3", ["LA VEGA"]),
    ("Jarabacoa",              "cibao",    85,  "3", "4", ["JARABACOA"]),
    ("San Francisco de Macorís","cibao",   75,  "3", "5", ["SAN FRANCISCO DE MACORIS", "SFM"]),
    ("Bonao",                  "cibao",    95,  "3", "3", ["BONAO", "MONSEÑOR NOUEL", "MON SR. NOUEL"]),
    ("Puerto Plata",           "norte",    70,  "3", "4", ["PUERTO PLATA"]),
    ("SD Polígono Central",    "sd",       155, "4", "2", []),
    ("SD Zona Este",           "sd",       160, "4", "3", ["SANTO DOMINGO ESTE", "SANTO DOMIGO ESTE"]),
    ("SD Zona Oeste",          "sd",       158, "4", "4", ["SANTO DOMINGO OESTE"]),
    ("SD Malecón",             "sd",       155, "4", "5", ["DISTRITO NACIONAL", "LOS RIOS, D. N ."]),
    ("Este — Higüey/Bávaro",   "este",     315, "4", "2", ["ALTAGRACIA", "HIGUEY", "BAVARO", "PUNTA CANA"]),
    ("La Romana",              "este",     265, "4", "3", ["LA ROMANA"]),
    ("Baní/San Juan",          "sur",      215, "4", "4", ["BANI", "SAN JUAN", "AZUA"]),
    ("Exportación",            "export",   0,   False, False, ["MIAMI"]),
]

# Ciudad genérica de Santo Domingo: se reparte a mano después, por dirección.
CIUDADES_SD = ["SANTO DOMINGO", "SANTO DOMINIGO"]

# ---------------------------------------------------------------------------
# 2. Perfiles y potenciales de ejemplo. Sólo para ver la ficha llena — el
#    potencial real lo estima el vendedor en la visita.
# ---------------------------------------------------------------------------
PERFILES = [
    ("Ganadero", "Diálogo directo", "No tolera excusas", "Visita mensual",
     "Empresa familiar. El dueño decide y contesta el teléfono él mismo; ir por "
     "el encargado alarga la venta. Prefiere que le confirmen la fecha de entrega "
     "antes de despachar. Paga puntual pero exige que la factura llegue con la "
     "mercancía."),
    ("Compra por volumen", "Negocia precio", "Pide cotización escrita",
     "Visita quincenal",
     "Compara siempre con dos proveedores antes de colocar. Responde bien a "
     "compromiso de fecha y mal a la improvisación. El encargado de compras "
     "decide hasta cierto monto; sobre eso pasa al dueño."),
    ("Cliente antiguo", "Trato formal", "Prefiere correo", "Visita mensual",
     "Relación de años, poco regateo. Manda la orden por correo y espera "
     "confirmación escrita. No le gusta que lo llamen fuera de horario."),
]


def sembrar(env):
    Ruta = env["res.partner.route"]
    Partner = env["res.partner"]

    # --- 1. Crear o actualizar las rutas -----------------------------------
    mapa_ciudad = {}
    creadas = actualizadas = 0
    for nombre, zona, km, costo, dia, ciudades in RUTAS:
        ruta = Ruta.search([("name", "=", nombre)], limit=1)
        vals = {
            "name": nombre, "zona": zona, "km_desde_planta": km,
            "costo_logistica_sugerido": costo or False,
            "dia_visita": dia or False, "activa": True,
        }
        if ruta:
            ruta.write(vals); actualizadas += 1
        else:
            ruta = Ruta.create(vals); creadas += 1
        for c in ciudades:
            mapa_ciudad[c.upper().strip()] = ruta

    print("Rutas: %s creadas, %s actualizadas" % (creadas, actualizadas))

    # --- 2. Asignar ruta por ciudad a quien no tiene ------------------------
    sin_ruta = Partner.search([
        ("customer_rank", ">", 0), ("ag_ruta", "=", False), ("city", "!=", False)])
    asignados = 0
    ruta_stgo = Ruta.search([("name", "=", "Santiago Centro")], limit=1)
    ruta_sd = Ruta.search([("name", "=", "SD Polígono Central")], limit=1)
    for p in sin_ruta:
        ciudad = (p.city or "").upper().strip()
        ruta = mapa_ciudad.get(ciudad)
        if not ruta:
            # Las seis grafías de "Santiago" caen todas en la misma zona;
            # dentro de Santiago hay que repartirlas a mano por sector.
            if "SANTIAGO" in ciudad:
                ruta = ruta_stgo
            elif "SANTO DOM" in ciudad or ciudad in CIUDADES_SD:
                ruta = ruta_sd
        if ruta:
            p.ag_ruta = ruta.id
            if ruta.km_desde_planta and not p.ag_km_planta:
                p.ag_km_planta = ruta.km_desde_planta
            if ruta.costo_logistica_sugerido and not p.ag_costo_logistica:
                p.ag_costo_logistica = ruta.costo_logistica_sugerido
            asignados += 1
    print("Clientes con ruta asignada por ciudad: %s de %s" % (asignados, len(sin_ruta)))

    # --- 3. Heredar km y costo en los que ya tenían ruta -------------------
    con_ruta = Partner.search([
        ("customer_rank", ">", 0), ("ag_ruta", "!=", False), ("ag_km_planta", "=", 0)])
    for p in con_ruta:
        if p.ag_ruta.km_desde_planta:
            p.ag_km_planta = p.ag_ruta.km_desde_planta
            p.ag_costo_logistica = p.ag_ruta.costo_logistica_sugerido or p.ag_costo_logistica
    print("Clientes que heredaron distancia de su ruta: %s" % len(con_ruta))

    # --- 4. Potencial y perfil de ejemplo en los diez mayores --------------
    env["res.partner"]._cron_recalcular_desempeno()
    top = Partner.search(
        [("customer_rank", ">", 0), ("ag_venta_12m", ">", 0)],
        order="ag_venta_12m desc", limit=10)
    for i, p in enumerate(top):
        # Potencial de ejemplo: entre 1.3 y 1.8 veces la compra actual.
        factor = 1.3 + (i % 4) * 0.15
        etiquetas = PERFILES[i % len(PERFILES)]
        p.write({
            "ag_potencial_mes": round(p.ag_venta_mensual * factor, -3),
            "ag_perfil_notas": "%s · %s · %s · %s\n\n%s\n\n[DATO DE PRUEBA — "
                               "reemplazar con lo que sepa el vendedor]"
                               % (etiquetas[0], etiquetas[1], etiquetas[2],
                                  etiquetas[3], etiquetas[4]),
        })
    print("Potencial y perfil de ejemplo en %s clientes" % len(top))

    # --- 5. Recalcular con todo puesto -------------------------------------
    Partner._cron_recalcular_desempeno()
    hoy = fields.Date.context_today(Partner)
    corte = hoy.replace(day=1) - __import__("datetime").timedelta(days=1)
    Partner.search([("customer_rank", ">", 0)]).mapped(
        "commercial_partner_id")._ag_guardar_snapshot(corte)

    evaluados = Partner.search_count([("ag_desemp_grado", "!=", False)])
    alertas = env["ag.desempeno.alerta"].search_count([("activa", "=", True)])
    print("\nListo. %s clientes evaluados, %s alertas abiertas." % (evaluados, alertas))
    print("Revise: Desempeño → Todos los clientes")


sembrar(env)   # noqa: F821  (env lo provee el shell de Odoo)
env.cr.commit()  # noqa: F821
