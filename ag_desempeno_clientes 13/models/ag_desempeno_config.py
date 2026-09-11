# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AgDesempenoConfig(models.Model):
    """Parámetros del módulo. Un registro por compañía.

    Todo lo calibrable vive aquí: pesos de las cinco dimensiones, cortes de
    grado, y umbrales de cada alerta. Cambiar un peso no exige tocar código.
    """
    _name = "ag.desempeno.config"
    _description = "Configuración de Desempeño de Clientes"
    _rec_name = "display_name"

    company_id = fields.Many2one(
        "res.company", string="Compañía", required=True,
        default=lambda self: self.env.company, ondelete="cascade")
    active = fields.Boolean(default=True)

    # Una sola configuración por compañía: el módulo lee la primera que
    # encuentra, así que dos filas serían dos verdades distintas.
    _sql_constraints = [
        ("company_uniq", "unique(company_id)",
         "Ya existe una configuración de Desempeño para esta compañía."),
    ]

    # Vive en res.company, pero se edita aquí: es lo único de la compañía que
    # cambia lo que el cliente lee, y se corrige donde se calibra el resto.
    nombre_publico = fields.Char(
        related="company_id.ag_nombre_publico", readonly=False,
        string="Nombre para clientes")

    # ------------------------------------------------------------------
    # Ventana de evaluación
    # ------------------------------------------------------------------
    meses_ventana = fields.Integer(
        string="Meses de la ventana", default=12, required=True,
        help="Ventana móvil sobre la que se evalúa el desempeño. "
             "12 meses absorbe la estacionalidad y no se reinicia en enero.")
    meses_tendencia_corta = fields.Integer(
        string="Meses del bloque de tendencia", default=3, required=True,
        help="Tamaño del bloque comparado contra el bloque anterior para "
             "medir la velocidad del cambio.")
    minimo_meses_para_score = fields.Integer(
        string="Meses mínimos con compra", default=3, required=True,
        help="Un cliente con menos meses de historia no recibe grado: se "
             "marca como 'nuevo' hasta acumular historia suficiente.")

    # ------------------------------------------------------------------
    # Pesos de las dimensiones (deben sumar 100)
    # ------------------------------------------------------------------
    peso_valor = fields.Integer(string="Peso · Valor", default=20, required=True)
    peso_rentabilidad = fields.Integer(string="Peso · Rentabilidad", default=25, required=True)
    peso_pago = fields.Integer(string="Peso · Comportamiento de pago", default=25, required=True)
    peso_consistencia = fields.Integer(string="Peso · Consistencia", default=20, required=True)
    peso_operativo = fields.Integer(string="Peso · Calidad operativa", default=10, required=True)
    peso_total = fields.Integer(string="Total", compute="_compute_peso_total")

    # ------------------------------------------------------------------
    # Cortes de grado
    # ------------------------------------------------------------------
    corte_a = fields.Integer(string="Grado A desde", default=80, required=True)
    corte_b = fields.Integer(string="Grado B desde", default=65, required=True)
    corte_c = fields.Integer(string="Grado C desde", default=45, required=True)

    # ------------------------------------------------------------------
    # Umbrales de alertas
    # ------------------------------------------------------------------
    dias_dormido = fields.Integer(
        string="Días sin comprar para 'dormido'", default=60, required=True)
    dias_dormido_critico = fields.Integer(
        string="Días sin comprar críticos", default=90, required=True)
    caida_deterioro = fields.Float(
        string="Caída % que dispara deterioro", default=20.0, required=True,
        help="Caída del bloque reciente contra el anterior, en porcentaje.")
    caida_deterioro_grave = fields.Float(
        string="Caída % de deterioro grave", default=40.0, required=True)
    dias_pago_estirado = fields.Integer(
        string="Días extra de pago que alertan", default=10, required=True,
        help="Aumento de los días de cobro reales frente al promedio histórico "
             "del propio cliente. Combinado con caída de volumen es la señal "
             "temprana más valiosa del módulo.")
    margen_minimo = fields.Float(
        string="Margen % mínimo aceptable", default=10.0, required=True)
    uso_credito_alerta = fields.Float(
        string="% de uso de crédito que alerta", default=85.0, required=True)
    dias_backlog_estancado = fields.Integer(
        string="Días de backlog sin despachar", default=15, required=True)
    dias_sin_facturar = fields.Integer(
        string="Días entregado sin facturar", default=7, required=True)
    dias_vencido_critico = fields.Integer(
        string="Días de vencimiento crítico", default=60, required=True)
    concentracion_alerta = fields.Float(
        string="% de la venta total que alerta por concentración",
        default=8.0, required=True)
    captura_minima = fields.Float(
        string="% mínimo de captura del potencial", default=60.0, required=True,
        help="Bajo este porcentaje se alerta que hay volumen en manos de otro "
             "proveedor. Sólo aplica a clientes con potencial estimado.")
    km_corta = fields.Float(
        string="Km hasta zona cercana (2%)", default=30.0, required=True)
    km_media = fields.Float(
        string="Km hasta zona intermedia (3%)", default=120.0, required=True,
        help="Sobre esta distancia se sugiere 4%. Es sólo una sugerencia: la "
             "ruta, el tipo de camión y la frecuencia pesan tanto como los "
             "kilómetros, por eso el porcentaje se fija a mano.")
    margen_sospechoso = fields.Float(
        string="Margen % que indica costeo dudoso", default=70.0, required=True,
        help="Sobre este margen la cifra casi siempre señala costo mal cargado, "
             "no rentabilidad real. Se marca para revisión y NO premia el score.")

    # ------------------------------------------------------------------
    # Distribución
    # ------------------------------------------------------------------
    dias_validez_enlace = fields.Integer(
        string="Días de validez del enlace público", default=30, required=True)
    base_url_publica = fields.Char(
        string="Base URL para enlaces",
        help="Si se deja vacío se usa web.base.url del sistema.")

    @api.depends("peso_valor", "peso_rentabilidad", "peso_pago",
                 "peso_consistencia", "peso_operativo")
    def _compute_peso_total(self):
        for rec in self:
            rec.peso_total = (rec.peso_valor + rec.peso_rentabilidad
                              + rec.peso_pago + rec.peso_consistencia
                              + rec.peso_operativo)

    @api.depends("company_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "Desempeño de clientes — %s" % (
                rec.company_id.name or "")

    @api.constrains("peso_valor", "peso_rentabilidad", "peso_pago",
                    "peso_consistencia", "peso_operativo")
    def _check_pesos(self):
        for rec in self:
            if rec.peso_total != 100:
                raise ValidationError(
                    "Los pesos de las cinco dimensiones deben sumar 100. "
                    "Actualmente suman %s." % rec.peso_total)

    @api.constrains("corte_a", "corte_b", "corte_c")
    def _check_cortes(self):
        for rec in self:
            if not (rec.corte_a > rec.corte_b > rec.corte_c > 0):
                raise ValidationError(
                    "Los cortes de grado deben ir de mayor a menor: "
                    "A > B > C > 0.")

    @api.model
    def get_config(self, company=None):
        """Devuelve la configuración de la compañía, creándola si no existe."""
        company = company or self.env.company
        cfg = self.search([("company_id", "=", company.id)], limit=1)
        if not cfg:
            cfg = self.create({"company_id": company.id})
        return cfg

    @api.model
    def action_abrir(self):
        """Abre la configuración de la compañía en vez de un registro nuevo.

        El menú apuntaba a la vista de formulario sin `res_id`, así que Odoo
        abría un registro en blanco cada vez: la calibración guardada no se
        veía, y guardar creaba una segunda configuración para la misma
        compañía. Con esto siempre se entra al registro que el módulo lee.
        """
        cfg = self.get_config()
        return {
            "type": "ir.actions.act_window",
            "name": "Configuración",
            "res_model": self._name,
            "res_id": cfg.id,
            "view_mode": "form",
            "target": "current",
            "context": {"form_view_initial_mode": "edit"},
        }
