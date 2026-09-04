# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

MESES = [
    ("1", "Enero"), ("2", "Febrero"), ("3", "Marzo"), ("4", "Abril"),
    ("5", "Mayo"), ("6", "Junio"), ("7", "Julio"), ("8", "Agosto"),
    ("9", "Septiembre"), ("10", "Octubre"), ("11", "Noviembre"), ("12", "Diciembre"),
]


class AgsEstacionalidad(models.Model):
    """Factores de estacionalidad calculados desde el historico propio.

    Los factores NO se cargan a mano con supuestos: se calculan desde las
    ventas reales de AG Supply en Odoo. Un factor de 1.20 en agosto significa
    que ese mes vende 20% por encima del promedio anual.

    En el mercado dominicano de tissue los ciclos conocidos son temporada
    escolar, fin de año, Semana Santa y el ciclo quincenal de pagos del pais.
    Pero la magnitud real de cada uno hay que medirla, no suponerla.
    """
    _name = "ags.estacionalidad"
    _description = "AG Intelligence - Factor de Estacionalidad"
    _order = "parametro_id, categoria_id, mes"

    parametro_id = fields.Many2one(
        "ags.parametro", string="Parametro", ondelete="cascade"
    )
    categoria_id = fields.Many2one(
        "product.category",
        string="Linea de producto",
        help="Dejar vacio para un factor global. La estacionalidad de papel "
             "higienico y de toallas institucionales no es la misma.",
    )
    mes = fields.Selection(MESES, string="Mes", required=True)
    factor = fields.Float(
        string="Factor",
        digits=(16, 4),
        required=True,
        default=1.0,
        help="1.00 = mes promedio. 1.20 = 20% por encima del promedio.",
    )
    anios_base = fields.Char(
        string="Años base",
        help="Periodo historico usado para calcular el factor. Ej: 2023-2025",
    )
    n_observaciones = fields.Integer(
        string="Observaciones",
        help="Cuantos años entraron al calculo. Con menos de 3 el factor es fragil.",
    )
    confiabilidad = fields.Selection(
        [
            ("alta", "Alta - 3 o mas años"),
            ("media", "Media - 2 años"),
            ("baja", "Baja - 1 año o estimado"),
        ],
        string="Confiabilidad",
        compute="_compute_confiabilidad",
        store=True,
    )
    fecha_calculo = fields.Date(
        string="Calculado el", default=fields.Date.context_today
    )
    notas = fields.Text(string="Notas")

    _sql_constraints = [
        (
            "factor_unico",
            "unique(parametro_id, categoria_id, mes)",
            "Ya existe un factor para esa combinacion de parametro, linea y mes.",
        ),
        (
            "factor_positivo",
            "check(factor > 0)",
            "El factor de estacionalidad debe ser mayor que cero.",
        ),
    ]

    @api.depends("n_observaciones")
    def _compute_confiabilidad(self):
        for rec in self:
            if rec.n_observaciones >= 3:
                rec.confiabilidad = "alta"
            elif rec.n_observaciones == 2:
                rec.confiabilidad = "media"
            else:
                rec.confiabilidad = "baja"

    @api.model
    def recalcular_desde_historico(self, parametro_id, anios=3):
        """Recalcula factores desde el historico real de Odoo.

        Se implementa en la fase de Demanda y Ventas. La firma queda definida
        aqui para que el resto del sistema pueda apoyarse en ella.
        """
        raise UserError(
            _("El calculo automatico de estacionalidad se implementa en la "
              "fase de Demanda y Ventas. Por ahora los factores se cargan "
              "desde el analisis de ventas historicas.")
        )


class AgsProyeccion(models.Model):
    """Proyecciones a futuro combinando tendencia y estacionalidad.

    Se guarda el valor proyectado junto al real cuando llega, para poder
    medir la precision del metodo. Un sistema de proyeccion que nunca se
    audita contra la realidad no mejora nunca.
    """
    _name = "ags.proyeccion"
    _description = "AG Intelligence - Proyeccion"
    _order = "fecha_periodo, parametro_id"

    parametro_id = fields.Many2one(
        "ags.parametro", string="Parametro", required=True, ondelete="cascade"
    )
    codigo_parametro = fields.Char(
        related="parametro_id.codigo", string="Codigo", store=True
    )
    unidad = fields.Selection(related="parametro_id.unidad", string="Unidad")

    fecha_periodo = fields.Date(string="Periodo proyectado", required=True)
    valor_proyectado = fields.Float(
        string="Valor proyectado", digits=(16, 2), required=True
    )
    valor_real = fields.Float(
        string="Valor real",
        digits=(16, 2),
        compute="_compute_real",
        store=True,
        help="Se llena solo cuando llega la medicion del periodo",
    )
    error_pct = fields.Float(
        string="Error (%)", compute="_compute_real", store=True, digits=(16, 2)
    )

    metodo = fields.Selection(
        [
            ("tendencia", "Tendencia lineal"),
            ("estacional", "Tendencia + estacionalidad"),
            ("manual", "Estimacion manual"),
            ("presupuesto", "Presupuesto aprobado"),
        ],
        string="Metodo",
        required=True,
        default="estacional",
    )
    confianza = fields.Selection(
        [
            ("alta", "Alta"),
            ("media", "Media"),
            ("baja", "Baja"),
        ],
        string="Confianza",
        default="media",
    )
    supuestos = fields.Text(
        string="Supuestos",
        help="Que se asumio para llegar a este numero. Obligatorio si la "
             "proyeccion alimenta decisiones de compra o de caja.",
    )
    aprobado = fields.Boolean(string="Aprobado", default=False)
    aprobado_por_id = fields.Many2one("res.users", string="Aprobado por", readonly=True)
    fecha_aprobacion = fields.Datetime(string="Fecha de aprobacion", readonly=True)

    @api.depends("fecha_periodo", "parametro_id")
    def _compute_real(self):
        for rec in self:
            medicion = self.env["ags.medicion"].search([
                ("parametro_id", "=", rec.parametro_id.id),
                ("fecha_periodo", "=", rec.fecha_periodo),
            ], limit=1)
            if medicion:
                rec.valor_real = medicion.valor
                if rec.valor_proyectado:
                    rec.error_pct = (
                        (medicion.valor - rec.valor_proyectado)
                        / abs(rec.valor_proyectado)
                    ) * 100.0
                else:
                    rec.error_pct = 0.0
            else:
                rec.valor_real = 0.0
                rec.error_pct = 0.0

    def action_aprobar(self):
        for rec in self:
            if not rec.supuestos:
                raise UserError(
                    _("Documente los supuestos antes de aprobar la proyeccion de %s.")
                    % rec.parametro_id.name
                )
            rec.write({
                "aprobado": True,
                "aprobado_por_id": self.env.user.id,
                "fecha_aprobacion": fields.Datetime.now(),
            })
        return True
