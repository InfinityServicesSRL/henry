# -*- coding: utf-8 -*-
import ast
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AgsHallazgo(models.Model):
    """Una instancia detectada de incumplimiento de una regla.

    TRES PROPIEDADES DE DISENO, y las tres existen para responder preguntas
    que hoy el modulo no sabe contestar:

    1. primera_deteccion NO SE PISA NUNCA. Ni al reabrir. La primera pregunta
       de cualquier auditor es "desde cuando lo saben", y una tabla que solo
       guarda el estado de hoy no puede contestarla. Un hallazgo que aparece,
       se cierra solo y vuelve no reinicia el reloj: sube reincidencias.

    2. UN HALLAZGO NO SE BORRA, SE CIERRA. Cuando la regla deja de detectarlo
       se marca cerrado_auto con fecha y motivo. Un hallazgo borrado es un
       hallazgo que nunca ocurrio, que es lo contrario de una auditoria.

    3. ACEPTAR UN RIESGO ES UN ACTO REGISTRADO, con motivo y usuario. Un
       hallazgo que a nadie le importa se cierra explicitamente, no se ignora.
       La lista que se puede ignorar deja de leerse, y esa atencion no se
       recupera despues.
    """
    _name = "ags.hallazgo"
    _description = "AG Intelligence - Hallazgo de auditoria"
    _inherit = ["mail.thread"]
    # Ordenado por antiguedad y no por gravedad: el selection de gravedad se
    # ordena alfabeticamente en SQL (danger, info, warning) y pondria las
    # informativas por encima de las advertencias. La gravedad se resuelve en
    # la vista con decoraciones; aqui manda desde cuando esta abierto.
    _order = "primera_deteccion asc, id"
    _rec_name = "sujeto"

    regla_id = fields.Many2one(
        "ags.regla", string="Regla", required=True,
        ondelete="cascade", index=True)
    codigo_regla = fields.Char(
        related="regla_id.codigo", string="Codigo", store=True)
    familia = fields.Selection(
        related="regla_id.familia", string="Familia", store=True)
    efecto = fields.Selection(related="regla_id.efecto", string="Efecto")
    enunciado = fields.Text(related="regla_id.enunciado", string="Que deberia ser cierto")
    por_que_importa = fields.Text(related="regla_id.por_que_importa")

    compania_id = fields.Many2one(
        "res.company", string="Compania", required=True, index=True,
        help="Obligatoria. La configuracion de valoracion, costeo y cuentas "
             "es company_dependent: un hallazgo sin compania no se puede "
             "corregir porque no se sabe donde mirar.",
    )
    clave = fields.Char(
        string="Clave", required=True, index=True,
        help="regla:sujeto. Es la base de la deduplicacion entre corridas.")
    sujeto = fields.Char(
        string="Sujeto", required=True,
        help="Como se llama lo senalado: una categoria, una cuenta, "
             "'278 fichas de producto'.")
    cantidad = fields.Integer(string="Registros que incumplen", default=0)

    modelo = fields.Char(string="Modelo de origen")
    dominio = fields.Char(string="Filtro de origen")

    gravedad = fields.Selection(
        related="regla_id.gravedad_base", string="Gravedad",
        store=True, index=True,
        help="La grada la declara la regla. No escala sola con los dias "
             "abiertos: un campo almacenado que dependa de la fecha de hoy "
             "nunca se recomputa, que es el defecto que ya tiene "
             "ags.benchmark.es_vigente. El escalado por antiguedad se decide "
             "al notificar, no al guardar.")

    estado = fields.Selection(
        [
            ("abierto", "Abierto"),
            ("en_curso", "En correccion"),
            ("aceptado", "Riesgo aceptado"),
            ("resuelto", "Resuelto"),
            ("cerrado_auto", "Cerrado automaticamente"),
        ],
        string="Estado", default="abierto", required=True,
        tracking=True, index=True)
    vivo = fields.Boolean(
        string="Vivo", compute="_compute_vivo", store=True,
        help="Abierto o en correccion. Es lo que consume la banda de "
             "confianza y el calculo de confiabilidad de los indicadores.")

    primera_deteccion = fields.Date(
        string="Detectado el", required=True, readonly=True,
        default=fields.Date.context_today,
        help="No se modifica nunca, ni al reabrir tras una reincidencia.")
    ultima_deteccion = fields.Date(string="Visto por ultima vez")
    dias_abierto = fields.Integer(
        string="Dias abierto", compute="_compute_dias_abierto")
    ultima_reincidencia = fields.Date(
        string="Ultima reincidencia", readonly=True,
        help="El dia en que volvio tras haberse cerrado. Sin esta fecha el "
             "aviso diario no puede distinguir 'reincidio hoy' de "
             "'reincidio en marzo y sigue abierto', y acabaria repitiendo "
             "las mismas reincidencias todos los dias.")
    reincidencias = fields.Integer(
        string="Reincidencias", default=0,
        help="Veces que se cerro solo y volvio a aparecer. Una reincidencia "
             "dice que la correccion no pego, que es informacion distinta de "
             "que el hallazgo siga abierto.")

    fecha_resolucion = fields.Date(string="Cerrado el", readonly=True)
    resuelto_por_id = fields.Many2one("res.users", string="Cerrado por", readonly=True)
    nota_cierre = fields.Text(string="Nota de cierre", tracking=True)
    motivo_aceptacion = fields.Text(string="Motivo de la aceptacion", tracking=True)

    responsable_id = fields.Many2one(
        "res.users", string="Responsable", compute="_compute_responsable",
        store=True, readonly=False,
        help="Se hereda de la regla y se puede reasignar caso por caso.")
    ajuste_id = fields.Many2one(
        "ags.ajuste", string="Ajuste de calculo",
        help="Cuando este hallazgo motivo aislar cuentas del calculo. Un "
             "ajuste sin hallazgo que lo justifique queda visiblemente "
             "huerfano, que es la pregunta correcta a hacerle a un ajuste.")
    parametro_ids = fields.Many2many(
        related="regla_id.parametro_ids", string="Indicadores que toca")

    _sql_constraints = [
        # Una sola fila por condicion y compania, para siempre. La
        # reconciliacion reabre la existente en vez de crear otra: es lo que
        # hace que primera_deteccion signifique algo.
        ("hallazgo_unico", "unique(clave, compania_id)",
         "Ya existe ese hallazgo para la compania."),
    ]

    @api.depends("estado")
    def _compute_vivo(self):
        for rec in self:
            rec.vivo = rec.estado in ("abierto", "en_curso")

    @api.depends("primera_deteccion", "estado", "fecha_resolucion")
    def _compute_dias_abierto(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            if not rec.primera_deteccion:
                rec.dias_abierto = 0
                continue
            hasta = hoy if rec.vivo else (rec.fecha_resolucion or hoy)
            rec.dias_abierto = (hasta - rec.primera_deteccion).days

    @api.depends("regla_id.responsable_id")
    def _compute_responsable(self):
        for rec in self:
            if not rec.responsable_id:
                rec.responsable_id = rec.regla_id.responsable_id

    @api.depends("codigo_regla", "sujeto")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "[%s] %s" % (rec.codigo_regla or "", rec.sujeto or "")

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def action_en_curso(self):
        self.write({"estado": "en_curso"})
        return True

    def action_aceptar(self):
        """Aceptar el riesgo. Exige motivo escrito (D15)."""
        for rec in self:
            if not (rec.motivo_aceptacion or "").strip():
                raise UserError(_(
                    "Para aceptar el riesgo hay que escribir el motivo. Un "
                    "hallazgo aceptado sin razon es un hallazgo ignorado con "
                    "otro nombre."))
        self.write({
            "estado": "aceptado",
            "resuelto_por_id": self.env.user.id,
        })
        return True

    def action_resuelto(self):
        """Cierre manual. La proxima corrida lo reabre si sigue ahi."""
        self.write({
            "estado": "resuelto",
            "fecha_resolucion": fields.Date.context_today(self),
            "resuelto_por_id": self.env.user.id,
        })
        return True

    def action_reabrir(self):
        self.write({
            "estado": "abierto",
            "fecha_resolucion": False,
            "resuelto_por_id": False,
        })
        return True

    def action_ver_origen(self):
        """Abre los registros que incumplen, con el mismo dominio con el que
        se detectaron. Lo que se abre no es una aproximacion, es la consulta
        original -- mismo criterio que ags.componente."""
        self.ensure_one()
        if not (self.modelo and self.dominio):
            raise UserError(_("Este hallazgo no declara registros de origen."))
        try:
            dominio = ast.literal_eval(self.dominio)
        except (ValueError, SyntaxError):
            _logger.warning(
                "ags.hallazgo %s: dominio ilegible %r", self.id, self.dominio)
            raise UserError(_("El filtro guardado no se puede interpretar."))
        return {
            "type": "ir.actions.act_window",
            "name": self.sujeto or self.display_name,
            "res_model": self.modelo,
            "domain": dominio,
            "view_mode": "list,form",
            "target": "current",
            "context": {"allowed_company_ids": [self.compania_id.id]},
        }
