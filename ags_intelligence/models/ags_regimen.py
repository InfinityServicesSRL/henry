# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class AgsRegimen(models.Model):
    """Regimen de datos: periodo durante el cual un proceso se registro de una
    forma consistente.

    ORIGEN DE ESTE MODELO: en agosto 2026 se midio la variacion de consumo
    contra estandar de la lista de materiales y salieron oscilaciones de 30 y
    40 puntos entre meses consecutivos. La causa no era el proceso: el modulo
    de fabricacion se implemento en serio en abril 2026, y los meses previos
    reflejan la curva de aprendizaje del registro, no la realidad de planta.

    Un mes atipico (cierre fiscal, ajuste puntual) se marca en ags.medicion.
    Un REGIMEN es distinto: parte el historico en dos mundos que no son
    comparables entre si. Antes del corte, el dato existe pero no significa
    lo mismo.

    Sin este concepto, cualquier promedio, tendencia o factor de estacionalidad
    mezcla mediciones de calidades distintas y produce numeros que parecen
    validos y no lo son.
    """
    _name = "ags.regimen"
    _description = "AG Intelligence - Regimen de Datos"
    _order = "fecha_inicio desc"

    name = fields.Char(string="Regimen", required=True)
    descripcion = fields.Text(
        string="Que cambio",
        help="Que se empezo a registrar distinto, o que proceso cambio",
    )
    fecha_inicio = fields.Date(string="Vigente desde", required=True)
    fecha_fin = fields.Date(
        string="Vigente hasta",
        help="Vacio significa que es el regimen actual",
    )
    tipo = fields.Selection(
        [
            ("implementacion", "Implementacion de modulo"),
            ("proceso", "Cambio de proceso operativo"),
            ("contable", "Reclasificacion contable"),
            ("sistema", "Migracion de sistema"),
            ("politica", "Cambio de politica"),
        ],
        string="Tipo de cambio",
        required=True,
    )
    parametro_ids = fields.Many2many(
        "ags.parametro",
        "ags_regimen_parametro_rel",
        "regimen_id",
        "parametro_id",
        string="Parametros afectados",
        help="Que indicadores dejan de ser comparables con el regimen anterior. "
             "Dejar vacio si afecta a todos.",
    )
    afecta_todos = fields.Boolean(
        string="Afecta todos los parametros",
        default=False,
    )
    datos_previos_validos = fields.Boolean(
        string="Datos previos siguen siendo validos",
        default=False,
        help="Marcar solo si el cambio no rompe la comparabilidad. "
             "Por defecto se asume que si la rompe.",
    )
    meses_maduracion = fields.Integer(
        string="Meses hasta considerar confiable",
        default=6,
        help="Cuantos periodos de operacion estable hacen falta antes de que "
             "los indicadores afectados puedan leerse como definitivos. "
             "Con menos, se marcan como medibles con reservas.",
    )
    notas = fields.Text(string="Notas")
    active = fields.Boolean(default=True)

    @api.constrains("fecha_inicio", "fecha_fin")
    def _check_fechas(self):
        for rec in self:
            if rec.fecha_fin and rec.fecha_fin < rec.fecha_inicio:
                raise ValidationError(
                    _("La fecha de fin no puede ser anterior a la de inicio.")
                )

    @api.model
    def regimen_vigente(self, fecha=None, parametro=None):
        """Devuelve el regimen activo en una fecha para un parametro."""
        fecha = fecha or fields.Date.context_today(self)
        dominio = [("fecha_inicio", "<=", fecha)]
        regs = self.search(dominio, order="fecha_inicio desc")
        for r in regs:
            if r.fecha_fin and r.fecha_fin < fecha:
                continue
            if r.afecta_todos or not parametro:
                return r
            if parametro in r.parametro_ids:
                return r
        return self.browse()

    @api.model_create_multi
    def create(self, vals_list):
        regs = super().create(vals_list)
        self.env["ags.parametro"].recalcular_madurez()
        return regs

    def write(self, vals):
        res = super().write(vals)
        self.env["ags.parametro"].recalcular_madurez()
        return res

    def name_get(self):
        return [
            (r.id, "%s (desde %s)" % (r.name, r.fecha_inicio or ""))
            for r in self
        ]
