# -*- coding: utf-8 -*-
"""Completitud de la ficha técnica y venta de los últimos 12 meses.

Los datos técnicos del producto ya existen en Odoo: `module_agsupply` define
41 campos ag_* sobre product.template. Lo que faltaba era saber cuáles están
llenos. Este archivo no inventa datos: mide los que hay y dice qué falta,
ordenado por lo que más se vende, para que llenar el catálogo empiece por
donde rinde.
"""
import re

from odoo import api, fields, models
from odoo.exceptions import UserError

# El núcleo: sin esto la ficha no dice nada útil y no se genera.
NUCLEO = [
    ("ag_tipo_de_papel", "Tipo de papel"),
    ("ag_n_de_capas", "N° de capas"),
    ("ag_largo_de_hoja", "Hojas / longitud del rollo"),
    ("ag_ancho", "Ancho"),
]

# Por encima de este largo el dato no puede ser una hoja: es un rollo. Nadie
# fabrica una servilleta de tres metros, y el catálogo tiene toallas de 700
# pies capturadas en el mismo campo porque es el único que hay.
CM_ES_ROLLO = 300.0

# El complemento: si está se imprime, si falta se lista. Una ficha sin
# gramaje sigue sirviendo para vender; una sin tipo de papel, no.
COMPLEMENTO = [
    ("ag_gramaje", "Gramaje"),
    ("ag_cantidad_de_hojas", "Hojas por fardo"),
    ("ag_color", "Color"),
    ("ag_doblez", "Doblez"),
    ("ag_paquete", "Unidades por fardo"),
    ("ag_marca", "Marca"),
    ("ag_materia_prima_utilizada", "Materia prima"),
    # image_128 y no image_1920: la miniatura existe si y sólo si hay
    # foto, y no obliga a cargar el original en cada cálculo.
    ("image_128", "Foto del producto"),
]

# El bloque de empaque se evalúa entero: hoy está vacío en todo el catálogo,
# y listar ocho líneas por producto convierte la auditoría en ruido.
EMPAQUE = [
    "ag_empaque_primario", "ag_empaque_secundario",
    "ag_peso_bruto_paquete", "ag_peso_bruto_caja",
    "ag_dimensiones_fardo_caja", "ag_fardos_cajas_por_estiba",
    "ag_estiba_por_palet", "ag_total_de_fardos_cajas_por_palet",
]

# "2 PLY", "2 capas", "3 Capas" dentro del nombre del producto.
CAPAS_EN_NOMBRE = re.compile(r"(\d)\s*(?:ply|capas?)\b", re.IGNORECASE)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # Sólo se cambia la etiqueta: el campo, su tipo y su ayuda siguen siendo
    # los de module_agsupply. «Largo de hoja» describía mal lo que el equipo
    # captura de verdad, y «Cantidad de hojas» no decía por unidad de qué.
    ag_largo_de_hoja = fields.Float(string="Hojas / longitud del rollo (cm)")
    ag_cantidad_de_hojas = fields.Integer(string="Hojas por fardo")

    # ------------------------------------------------------------------
    # Completitud
    # ------------------------------------------------------------------
    ag_ficha_estado = fields.Selection(
        [("completa", "Completa"),
         ("parcial", "Parcial"),
         ("sin_datos", "Sin datos")],
        string="Estado de la ficha", compute="_compute_ag_ficha", store=True,
        help="Completa: tiene todo. Parcial: se puede generar, pero le "
             "faltan datos. Sin datos: le falta el núcleo y no se genera.")
    ag_ficha_faltantes = fields.Char(
        string="Falta por completar", compute="_compute_ag_ficha", store=True)
    ag_ficha_faltan_n = fields.Integer(
        string="Campos vacíos", compute="_compute_ag_ficha", store=True,
        help="Cuántos datos faltan para que la ficha quede completa.")
    ag_ficha_alerta = fields.Char(
        string="Revisar", compute="_compute_ag_ficha", store=True,
        help="Contradicciones entre el nombre del producto y sus campos.")

    ag_venta_12m = fields.Float(
        string="Venta 12M", readonly=True, digits=(16, 2), index=True,
        help="Facturación neta de los últimos doce meses. Ordena la "
             "auditoría: primero los productos que más pesan.")

    @api.depends(
        "ag_tipo_de_papel", "ag_n_de_capas", "ag_largo_de_hoja", "ag_ancho",
        "ag_gramaje", "ag_cantidad_de_hojas", "ag_color", "ag_doblez",
        "ag_paquete", "ag_marca", "ag_materia_prima_utilizada", "image_128",
        "ag_empaque_primario", "ag_empaque_secundario", "name",
    )
    def _compute_ag_ficha(self):
        for prod in self:
            faltan_nucleo = [et for campo, et in NUCLEO if not prod[campo]]
            faltan_resto = [et for campo, et in COMPLEMENTO if not prod[campo]]
            if not any(prod[c] for c in EMPAQUE):
                faltan_resto.append("Empaque y logística (%s campos)" % len(EMPAQUE))

            todo = faltan_nucleo + faltan_resto
            prod.ag_ficha_faltan_n = len(todo)
            prod.ag_ficha_faltantes = ", ".join(todo)
            if faltan_nucleo:
                prod.ag_ficha_estado = "sin_datos"
            elif faltan_resto:
                prod.ag_ficha_estado = "parcial"
            else:
                prod.ag_ficha_estado = "completa"
            prod.ag_ficha_alerta = prod._ag_contradicciones()

    def _ag_medida(self):
        """Cómo se llama y cómo se lee la medida, según lo que sea el producto.

        El mismo campo guarda el largo de una hoja y la longitud de un rollo,
        porque en el catálogo conviven servilletas y toallas de 700 pies. El
        documento no puede imprimir «medida de hoja: 21,336 cm»: decide por la
        magnitud y lo dice en metros y pies, que es como se compra un rollo.
        """
        self.ensure_one()
        largo = self.ag_largo_de_hoja or 0.0
        ancho = self.ag_ancho or 0.0
        if not largo:
            return {}
        if largo >= CM_ES_ROLLO:
            valor = "%.1f m (%.0f pies)" % (largo / 100.0, largo / 30.48)
            if ancho:
                valor += " · ancho %g cm" % ancho
            return {"etiqueta": "Longitud del rollo", "valor": valor}
        if not ancho:
            return {"etiqueta": "Largo de hoja", "valor": "%g cm" % largo}
        return {"etiqueta": "Medida de hoja", "valor": "%g × %g cm" % (largo, ancho)}

    def _ag_contradicciones(self):
        """Choques entre lo que dice el nombre y lo que dicen los campos.

        Vale la pena revisarlo antes de imprimir: un documento que se llama
        «2 PLY» y en la tabla dice «1 capa» destruye más confianza de la que
        construye toda la ficha.
        """
        self.ensure_one()
        avisos = []
        m = CAPAS_EN_NOMBRE.search(self.name or "")
        if m and self.ag_n_de_capas and int(m.group(1)) != self.ag_n_de_capas:
            avisos.append(
                "El nombre dice %s capas y el campo dice %s"
                % (m.group(1), self.ag_n_de_capas))
        elif m and not self.ag_n_de_capas:
            avisos.append("El nombre dice %s capas y el campo está vacío" % m.group(1))
        return " · ".join(avisos)

    # ------------------------------------------------------------------
    # Venta de los últimos doce meses
    # ------------------------------------------------------------------
    @api.model
    def _ag_recalcular_ventas(self):
        """Facturación neta 12M por producto, en una sola consulta.

        Netas: las notas de crédito restan. Se lee de las líneas de factura
        publicadas, no de los pedidos, porque lo que cuenta para priorizar es
        lo que se facturó.
        """
        self.env.cr.execute("""
            SELECT pp.product_tmpl_id,
                   SUM(CASE WHEN am.move_type = 'out_refund'
                            THEN -aml.price_subtotal ELSE aml.price_subtotal END)
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
              JOIN product_product pp ON pp.id = aml.product_id
             WHERE am.state = 'posted'
               AND am.move_type IN ('out_invoice', 'out_refund')
               AND am.invoice_date >= (CURRENT_DATE - INTERVAL '12 months')
               -- display_type = 'product', no IS NULL: en Odoo 17+ las líneas
               -- de producto llevan 'product' y las de costo llevan 'cogs'.
               -- Con IS NULL la consulta no devolvía una sola fila.
               AND aml.display_type = 'product'
          GROUP BY pp.product_tmpl_id
        """)
        ventas = dict(self.env.cr.fetchall())
        # Poner en cero los que dejaron de venderse: si no, un producto
        # descontinuado se queda arriba en la auditoría para siempre.
        for prod in self.search([("sale_ok", "=", True)]):
            nuevo = round(ventas.get(prod.id, 0.0) or 0.0, 2)
            if prod.ag_venta_12m != nuevo:
                prod.ag_venta_12m = nuevo
        return True

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_ag_imprimir_ficha(self):
        self.ensure_one()
        if self.ag_ficha_estado == "sin_datos":
            raise UserError(
                "A %s le falta el núcleo de la ficha: %s.\n\n"
                "Complételo en la pestaña «Ficha técnica» y vuelva a intentar."
                % (self.display_name, self.ag_ficha_faltantes))
        return self.env.ref(
            "ag_desempeno_clientes.report_ag_ficha_tecnica_action").report_action(self)

    def action_ag_enviar_ficha(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Enviar ficha técnica",
            "res_model": "ag.envio.ficha",
            "view_mode": "form",
            "target": "new",
            "context": {"active_ids": self.ids, "active_model": self._name},
        }
