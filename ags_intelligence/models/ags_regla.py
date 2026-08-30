# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class AgsRegla(models.Model):
    """Catalogo de invariantes: que DEBE ser cierto en el ERP.

    Es a la auditoria lo que ags.parametro es a la medicion, y la diferencia
    entre las dos tablas no es de forma sino de naturaleza:

      - Un PARAMETRO mide una magnitud continua que sube y baja, tiene banda
        tolerable y solo significa algo en serie: margen, DSO, dias de
        inventario.
      - Una REGLA afirma un hecho binario y censal. Una categoria de producto
        valora en tiempo real o no valora; no valora "un poco".

    Meter una causa de configuracion dentro de un parametro la disfraza de
    porcentaje. "El 4.68% de los productos tiene control de recepcion" invita
    a preguntar si 4.68 es bueno o malo, cuando la respuesta correcta es
    "278 fichas estan mal y aqui esta la lista".

    POR QUE ESTA CAPA EXISTE. El sintoma contable siempre llega tarde y caro.
    Los 272 dias de antiguedad del saldo de bienes recibidos no facturados son
    la factura de una casilla mal puesta en 278 fichas de producto. Los 25 SKU
    sin asiento de costo de ventas son la factura de una categoria creada con
    el valor por defecto de Odoo. En los dos casos la causa era detectable el
    dia uno, gratis, y nadie la estaba mirando.
    """
    _name = "ags.regla"
    _description = "AG Intelligence - Regla de auditoria"
    _order = "familia, codigo"

    codigo = fields.Char(
        string="Codigo", required=True,
        help="Identificador tecnico estable. Ej: CATEG_CUENTA_PROHIBIDA.",
    )
    name = fields.Char(string="Regla", required=True)
    enunciado = fields.Text(
        string="Que deberia ser cierto", required=True,
        help="Redactado en POSITIVO y en presente: 'Toda categoria con "
             "productos almacenables valora en tiempo real'. Escribir la "
             "regla como afirmacion y no como defecto obliga a definir el "
             "estado correcto, que es lo que se va a auditar.",
    )
    por_que_importa = fields.Text(
        string="Por que importa",
        help="La consecuencia de incumplirla. Es lo que lee el auditor para "
             "decidir si vale la pena atenderla.",
    )
    familia = fields.Selection(
        [
            ("configuracion", "Configuracion"),
            ("habito", "Habito de registro"),
            ("pendiente", "Pendiente acumulado"),
            ("integridad", "Integridad del dato"),
        ],
        string="Familia", required=True, default="configuracion",
        help="No es decorativa: determina a quien se le pide la correccion y "
             "que se le pide. Una CONFIGURACION es una casilla que se arregla "
             "una vez. Un HABITO no se arregla configurando: requiere proceso "
             "y gente. Un PENDIENTE se resuelve trabajando. La INTEGRIDAD es "
             "un dato imposible, no solo indeseable, y no admite tolerancia.",
    )
    metodo_tecnico = fields.Char(
        string="Metodo tecnico", required=True,
        help="Nombre del metodo Python en ags.auditor que evalua la regla. "
             "Una regla cuyo metodo no existe se salta en silencio, igual que "
             "un parametro sin calculador.",
    )
    gravedad_base = fields.Selection(
        [("danger", "Grave"), ("warning", "Advertencia"), ("info", "Informativa")],
        string="Gravedad", required=True, default="warning",
    )
    tolerancia = fields.Integer(
        string="Tolerancia", default=0,
        help="Ocurrencias que se toleran antes de abrir un hallazgo. Vive "
             "aqui y no en el codigo, por la misma razon que los umbrales de "
             "los indicadores viven en el benchmark (D1). Las reglas de "
             "integridad deben quedarse en cero: un dato imposible no tiene "
             "cantidad aceptable.",
    )
    efecto = fields.Selection(
        [
            ("invalida", "Invalida los indicadores"),
            ("degrada", "Los degrada a lectura con reserva"),
            ("informa", "Solo informa"),
        ],
        string="Efecto sobre los indicadores", required=True, default="degrada",
        help="El valor de la auditoria no esta en la lista de defectos: esta "
             "en saber QUE NUMERO no se puede leer por culpa de cual.",
    )
    parametro_ids = fields.Many2many(
        "ags.parametro", string="Indicadores que toca",
        help="Los indicadores cuyo calculo depende de que esta regla se "
             "cumpla. Dejarlo vacio es valido en las reglas informativas y es "
             "la excepcion, no la norma.",
    )
    por_compania = fields.Boolean(
        string="Evaluar por compania", default=False,
        help="Marcar cuando la regla lee campos company_dependent -- "
             "valoracion, costeo, cuentas de categoria, cuentas de diario. "
             "Sin esto la regla mide la compania activa del usuario que corrio "
             "la evaluacion y parece un dato de la empresa (D12).",
    )
    frecuencia = fields.Selection(
        [("diaria", "Diaria"), ("mensual", "Mensual")],
        string="Frecuencia", required=True, default="diaria",
        help="Las reglas de configuracion son consultas baratas sobre tablas "
             "pequenas y corren a diario: una categoria mal creada tiene que "
             "gritar el mismo dia, no el 30. Las que barren contabilidad "
             "completa van en mensual, con la corrida de mediciones.",
    )
    responsable_id = fields.Many2one(
        "res.users", string="Responsable",
        help="Quien responde por corregirla. Los hallazgos sin responsable "
             "caen en Gerencia.",
    )
    activa = fields.Boolean(string="Activa", default=True)

    hallazgo_ids = fields.One2many("ags.hallazgo", "regla_id", string="Hallazgos")
    n_abiertos = fields.Integer(
        string="Abiertos", compute="_compute_n_abiertos")
    ultima_evaluacion = fields.Datetime(string="Evaluada por ultima vez", readonly=True)

    _sql_constraints = [
        ("regla_codigo_unico", "unique(codigo)",
         "El codigo de la regla debe ser unico."),
    ]

    @api.constrains("codigo")
    def _check_codigo(self):
        for rec in self:
            if rec.codigo and (" " in rec.codigo or not rec.codigo.isupper()):
                raise ValidationError(
                    _("El codigo debe ir en mayusculas y sin espacios. "
                      "Ej: CATEG_CUENTA_PROHIBIDA"))

    @api.constrains("familia", "tolerancia")
    def _check_tolerancia_integridad(self):
        for rec in self:
            if rec.familia == "integridad" and rec.tolerancia:
                raise ValidationError(
                    _("Una regla de integridad no admite tolerancia: un dato "
                      "imposible no tiene cantidad aceptable."))

    @api.depends("hallazgo_ids.estado")
    def _compute_n_abiertos(self):
        for rec in self:
            rec.n_abiertos = len(rec.hallazgo_ids.filtered(
                lambda h: h.estado in ("abierto", "en_curso")))

    # name_get() no existe desde Odoo 17. Se declara el compute, que es lo que
    # le falta a ags.benchmark y por eso sus M2O muestran "ags.benchmark,42".
    @api.depends("codigo", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "[%s] %s" % (rec.codigo or "", rec.name or "")

    def action_evaluar(self):
        """Evalua solo estas reglas, sin esperar al cron."""
        return self.env["ags.auditor"].evaluar_reglas(
            codigos=self.mapped("codigo"))

    def action_ver_hallazgos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Hallazgos de %s") % self.name,
            "res_model": "ags.hallazgo",
            "domain": [("regla_id", "=", self.id)],
            "view_mode": "list,form",
            "target": "current",
        }
