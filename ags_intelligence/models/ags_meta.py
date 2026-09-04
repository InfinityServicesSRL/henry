# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

MESES = [
    ("1", "Enero"), ("2", "Febrero"), ("3", "Marzo"), ("4", "Abril"),
    ("5", "Mayo"), ("6", "Junio"), ("7", "Julio"), ("8", "Agosto"),
    ("9", "Septiembre"), ("10", "Octubre"), ("11", "Noviembre"), ("12", "Diciembre"),
]


class AgsMeta(models.Model):
    """Meta interna por parametro, periodo y dimension.

    TRES REFERENCIAS DISTINTAS, NO INTERCAMBIABLES:
      - BASELINE   : donde se arranco (ags.baseline, congelado)
      - BENCHMARK  : que hace la industria (ags.benchmark, externo)
      - META       : que se decidio alcanzar (este modelo, interno)

    Una empresa puede estar por encima del benchmark y por debajo de su
    meta, o al reves. Confundirlos hace que nadie sepa contra que se esta
    midiendo.

    DISENO PENSADO PARA COMPENSACION VARIABLE: aunque hoy las metas son solo
    referencia de gestion, existe la intencion de atar parte de las comisiones
    a su cumplimiento. Por eso el modelo lleva desde el inicio trazabilidad
    completa, aprobacion explicita y bloqueo del periodo una vez medido.

    Agregar esa auditoria despues obligaria a reconstruir el historico, y
    justamente cuando el numero empieza a tener consecuencias economicas es
    cuando su credibilidad no admite dudas.
    """
    _name = "ags.meta"
    _description = "AG Intelligence - Meta de Gestion"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "anio desc, mes desc, parametro_id"
    _rec_name = "display_name"

    parametro_id = fields.Many2one(
        "ags.parametro", string="Parametro", required=True,
        ondelete="restrict", tracking=True,
    )
    codigo_parametro = fields.Char(
        related="parametro_id.codigo", string="Codigo", store=True
    )
    unidad = fields.Selection(related="parametro_id.unidad", string="Unidad")
    direccion = fields.Selection(related="parametro_id.direccion")

    anio = fields.Integer(
        string="Año", required=True, tracking=True,
        default=lambda self: fields.Date.context_today(self).year,
    )
    mes = fields.Selection(
        MESES, string="Mes", required=True, tracking=True,
        default=lambda self: str(fields.Date.context_today(self).month),
    )
    fecha_cierre = fields.Date(
        string="Cierre del periodo", compute="_compute_fecha_cierre", store=True,
    )

    # ------------------------------------------------------------------
    # Dimension
    # ------------------------------------------------------------------

    dimension = fields.Selection(
        [
            ("global", "Global de la empresa"),
            ("vendedor", "Por vendedor"),
            ("mercado", "Por mercado"),
        ],
        string="Dimension", required=True, default="global", tracking=True,
    )
    vendedor_id = fields.Many2one(
        "res.users", string="Vendedor", tracking=True,
        domain="[('share','=',False)]",
    )
    mercado_id = fields.Many2one("ags.mercado", string="Mercado", tracking=True)

    # ------------------------------------------------------------------
    # Valor y estado
    # ------------------------------------------------------------------

    valor = fields.Float(
        string="Meta", digits=(16, 2), required=True, tracking=True,
    )
    valor_minimo = fields.Float(
        string="Umbral minimo", digits=(16, 2), tracking=True,
        help="Por debajo de este valor se considera incumplimiento. Si la "
             "meta llega a atarse a compensacion variable, este es el piso "
             "para acceder al bono.",
    )
    estado = fields.Selection(
        [
            ("borrador", "Borrador"),
            ("aprobada", "Aprobada"),
            ("cerrada", "Cerrada"),
        ],
        string="Estado", default="borrador", required=True, tracking=True,
    )
    aprobada_por_id = fields.Many2one(
        "res.users", string="Aprobada por", readonly=True, tracking=True
    )
    fecha_aprobacion = fields.Datetime(
        string="Fecha de aprobacion", readonly=True, tracking=True
    )
    cerrada_por_id = fields.Many2one(
        "res.users", string="Cerrada por", readonly=True
    )
    fecha_cierre_registro = fields.Datetime(
        string="Fecha de cierre", readonly=True
    )
    justificacion = fields.Text(
        string="Justificacion",
        tracking=True,
        help="En que se basa esta meta. Obligatoria al aprobar: una meta sin "
             "sustento es un numero que nadie puede defender despues.",
    )

    # ------------------------------------------------------------------
    # Resultado
    # ------------------------------------------------------------------

    valor_real = fields.Float(
        string="Real", compute="_compute_resultado", store=True, digits=(16, 2),
    )
    cumplimiento = fields.Float(
        string="Cumplimiento (%)", compute="_compute_resultado", store=True,
        digits=(5, 2),
    )
    brecha = fields.Float(
        string="Brecha", compute="_compute_resultado", store=True, digits=(16, 2),
    )
    semaforo = fields.Selection(
        [
            ("verde", "Cumplida"),
            ("amarillo", "Parcial"),
            ("rojo", "Incumplida"),
            ("sin_dato", "Sin medir"),
        ],
        string="Resultado", compute="_compute_resultado", store=True,
    )
    display_name = fields.Char(compute="_compute_display_name", store=True)

    _sql_constraints = [
        ("meta_unica",
         "unique(parametro_id, anio, mes, dimension, vendedor_id, mercado_id)",
         "Ya existe una meta para ese parametro, periodo y dimension."),
    ]

    # ------------------------------------------------------------------
    # Computados
    # ------------------------------------------------------------------

    @api.depends("anio", "mes")
    def _compute_fecha_cierre(self):
        for rec in self:
            if rec.anio and rec.mes:
                inicio = fields.Date.to_date("%s-%02d-01" % (rec.anio, int(rec.mes)))
                rec.fecha_cierre = inicio + relativedelta(months=1, days=-1)
            else:
                rec.fecha_cierre = False

    @api.depends("parametro_id", "anio", "mes", "dimension",
                 "vendedor_id", "mercado_id")
    def _compute_display_name(self):
        for rec in self:
            partes = [rec.parametro_id.codigo or ""]
            if rec.dimension == "vendedor" and rec.vendedor_id:
                partes.append(rec.vendedor_id.name)
            elif rec.dimension == "mercado" and rec.mercado_id:
                partes.append(rec.mercado_id.name)
            partes.append("%s/%s" % (rec.mes or "", rec.anio or ""))
            rec.display_name = " · ".join(p for p in partes if p)

    @api.depends("valor", "fecha_cierre", "parametro_id", "dimension",
                 "vendedor_id", "mercado_id", "valor_minimo")
    def _compute_resultado(self):
        """Compara la meta contra la medicion real del periodo.

        Solo las metas GLOBALES se resuelven automaticamente desde
        ags.medicion. Las dimensionadas por vendedor o mercado requieren el
        calculo dimensional, que se implementa aparte: registrar aqui un
        valor sin poder verificarlo seria peor que dejarlo vacio.
        """
        Medicion = self.env["ags.medicion"]
        for rec in self:
            rec.valor_real = 0.0
            rec.cumplimiento = 0.0
            rec.brecha = 0.0
            rec.semaforo = "sin_dato"
            if not rec.parametro_id or not rec.fecha_cierre:
                continue
            if rec.dimension != "global":
                continue
            med = Medicion.search([
                ("parametro_id", "=", rec.parametro_id.id),
                ("fecha_periodo", "=", rec.fecha_cierre),
            ], limit=1)
            if not med:
                continue
            rec.valor_real = med.valor
            rec.brecha = med.valor - rec.valor
            if rec.valor:
                if rec.direccion == "menor_mejor":
                    rec.cumplimiento = (rec.valor / med.valor * 100.0) if med.valor else 0.0
                else:
                    rec.cumplimiento = (med.valor / rec.valor) * 100.0
            rec.semaforo = rec._evaluar(med.valor)

    def _evaluar(self, real):
        """Verde si alcanza la meta, amarillo entre el umbral y la meta."""
        self.ensure_one()
        if self.direccion == "menor_mejor":
            if real <= self.valor:
                return "verde"
            if self.valor_minimo and real <= self.valor_minimo:
                return "amarillo"
            return "rojo"
        if real >= self.valor:
            return "verde"
        if self.valor_minimo and real >= self.valor_minimo:
            return "amarillo"
        return "rojo"

    # ------------------------------------------------------------------
    # Validaciones
    # ------------------------------------------------------------------

    @api.constrains("dimension", "vendedor_id", "mercado_id")
    def _check_dimension(self):
        for rec in self:
            if rec.dimension == "vendedor" and not rec.vendedor_id:
                raise ValidationError(_("Indique el vendedor de la meta."))
            if rec.dimension == "mercado" and not rec.mercado_id:
                raise ValidationError(_("Indique el mercado de la meta."))
            if rec.dimension == "global" and (rec.vendedor_id or rec.mercado_id):
                raise ValidationError(
                    _("Una meta global no puede tener vendedor ni mercado."))

    @api.constrains("valor_minimo", "valor", "direccion")
    def _check_umbral(self):
        for rec in self:
            if not rec.valor_minimo:
                continue
            if rec.direccion == "mayor_mejor" and rec.valor_minimo > rec.valor:
                raise ValidationError(
                    _("El umbral minimo no puede superar la meta."))
            if rec.direccion == "menor_mejor" and rec.valor_minimo < rec.valor:
                raise ValidationError(
                    _("En un parametro donde menor es mejor, el umbral debe "
                      "ser mayor que la meta."))

    # ------------------------------------------------------------------
    # Flujo
    # ------------------------------------------------------------------

    def action_aprobar(self):
        for rec in self:
            if rec.estado != "borrador":
                raise UserError(_("Solo se aprueban metas en borrador."))
            if not rec.justificacion:
                raise UserError(
                    _("Documente la justificacion antes de aprobar. Una meta "
                      "sin sustento es un numero que nadie puede defender "
                      "cuando llegue el momento de evaluar el resultado."))
            rec.write({
                "estado": "aprobada",
                "aprobada_por_id": self.env.user.id,
                "fecha_aprobacion": fields.Datetime.now(),
            })
        return True

    def action_volver_borrador(self):
        for rec in self:
            if rec.estado == "cerrada":
                raise UserError(
                    _("Una meta cerrada no vuelve a borrador. El periodo ya "
                      "fue medido: modificarla ahora invalidaria la "
                      "comparacion."))
            rec.write({
                "estado": "borrador",
                "aprobada_por_id": False,
                "fecha_aprobacion": False,
            })
        return True

    def action_cerrar(self):
        """Cierra la meta tras medir el periodo. Es irreversible."""
        for rec in self:
            if rec.estado != "aprobada":
                raise UserError(_("Solo se cierran metas aprobadas."))
            rec.write({
                "estado": "cerrada",
                "cerrada_por_id": self.env.user.id,
                "fecha_cierre_registro": fields.Datetime.now(),
            })
        return True

    def write(self, vals):
        """Protege el valor de metas cerradas.

        Una vez medido el periodo, cambiar la meta cambiaria el resultado
        de la evaluacion. Esa puerta debe estar cerrada desde antes de que
        haya consecuencias economicas, no despues.
        """
        protegidos = {"valor", "valor_minimo", "anio", "mes", "parametro_id",
                      "dimension", "vendedor_id", "mercado_id"}
        for rec in self:
            if rec.estado == "cerrada" and protegidos & set(vals.keys()):
                raise UserError(
                    _("La meta de %s esta cerrada y sus valores no se pueden "
                      "modificar.") % rec.display_name)
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.estado == "cerrada":
                raise UserError(
                    _("No se puede eliminar una meta cerrada: es parte del "
                      "registro historico de evaluacion."))
        return super().unlink()

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def action_duplicar_anio(self):
        """Copia las metas del año a otro, para armar el plan del siguiente."""
        self.ensure_one()
        nueva = self.copy({
            "anio": self.anio + 1,
            "estado": "borrador",
            "aprobada_por_id": False,
            "fecha_aprobacion": False,
            "cerrada_por_id": False,
            "fecha_cierre_registro": False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "ags.meta",
            "res_id": nueva.id,
            "view_mode": "form",
        }

    @api.model
    def cerrar_periodo(self, anio, mes):
        """Cierra en lote las metas aprobadas de un periodo ya medido."""
        metas = self.search([
            ("anio", "=", anio), ("mes", "=", str(mes)), ("estado", "=", "aprobada"),
        ])
        metas.action_cerrar()
        return len(metas)
