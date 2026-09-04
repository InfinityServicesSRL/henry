# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval

# A partir de aqui la lista de codigos deja de informar y estorba.
LIMITE_CODIGOS = 4


class AgsComponente(models.Model):
    """Cada pieza que compone el valor de una medicion.

    Un indicador que no puede explicarse no puede auditarse. Este modelo
    guarda, para cada medicion, de que cuentas salio y con que dominio se
    consultaron, de modo que desde el numero se pueda llegar a los apuntes
    que lo forman.

    Se registra en el momento del calculo y no se recalcula despues: si un
    asiento cambia manana, el componente sigue mostrando contra que se
    calculo aquel dia. Esa es justamente la propiedad que hace auditable
    una cifra.
    """
    _name = "ags.componente"
    _description = "AG Intelligence - Componente de calculo"
    _order = "medicion_id, secuencia, id"

    medicion_id = fields.Many2one(
        "ags.medicion", string="Medicion", required=True,
        ondelete="cascade", index=True,
    )
    parametro_id = fields.Many2one(
        related="medicion_id.parametro_id", string="Parametro", store=True)
    fecha_periodo = fields.Date(
        related="medicion_id.fecha_periodo", string="Periodo", store=True)

    secuencia = fields.Integer(string="Orden", default=10)
    rol = fields.Char(
        string="Concepto", required=True,
        help="Que representa esta pieza dentro de la formula del indicador",
    )
    tipo = fields.Selection(
        [
            ("contable", "Saldo contable"),
            ("conteo", "Conteo de registros"),
            ("derivado", "Valor derivado"),
        ],
        string="Tipo", default="contable", required=True,
    )
    valor = fields.Float(string="Valor", digits=(16, 2))

    cuenta_ids = fields.Many2many("account.account", string="Cuentas")
    cuentas_codigos = fields.Char(
        string="Codigos", compute="_compute_cuentas_codigos", store=True)
    cuentas_resumen = fields.Char(
        string="Cuentas", compute="_compute_cuentas_resumen", store=True)
    fecha_desde = fields.Date(string="Desde")
    fecha_hasta = fields.Date(string="Hasta")

    modelo = fields.Char(string="Modelo de origen")
    dominio = fields.Char(string="Filtro aplicado")
    notas = fields.Char(string="Detalle")

    @api.depends("cuenta_ids")
    def _compute_cuentas_codigos(self):
        for rec in self:
            rec.cuentas_codigos = ", ".join(
                sorted(c.code or "" for c in rec.cuenta_ids))

    @api.depends("cuenta_ids")
    def _compute_cuentas_resumen(self):
        """Version corta para la tabla del cockpit.

        Un indicador puede apoyarse en 87 cuentas. Volcarlas todas en una
        celda convierte la fila en un muro de digitos y esconde lo unico que
        el lector busca ahi, que es el importe. El detalle completo sigue
        disponible en el tooltip y en la ficha del componente.
        """
        for rec in self:
            codigos = sorted(c.code or "" for c in rec.cuenta_ids)
            if not codigos:
                rec.cuentas_resumen = ""
            elif len(codigos) <= LIMITE_CODIGOS:
                rec.cuentas_resumen = ", ".join(codigos)
            else:
                rec.cuentas_resumen = "%s cuentas (%s ... %s)" % (
                    len(codigos), codigos[0], codigos[-1])

    def action_ver_origen(self):
        """Abre los registros que forman este componente.

        Para un saldo contable se reconstruye el mismo dominio con el que se
        calculo: las mismas cuentas, el mismo rango y solo asientos
        publicados. Lo que se abre no es una aproximacion, es la consulta
        original.
        """
        self.ensure_one()

        if self.tipo == "contable" and self.cuenta_ids:
            dominio = [
                ("account_id", "in", self.cuenta_ids.ids),
                ("parent_state", "=", "posted"),
            ]
            if self.fecha_desde:
                dominio.append(("date", ">=", self.fecha_desde))
            if self.fecha_hasta:
                dominio.append(("date", "<=", self.fecha_hasta))
            return {
                "type": "ir.actions.act_window",
                "name": _("Apuntes de %s") % self.rol,
                "res_model": "account.move.line",
                "domain": dominio,
                "view_mode": "list,form",
                "context": {"search_default_group_by_account": 1},
                "target": "current",
            }

        if self.modelo and self.dominio:
            return {
                "type": "ir.actions.act_window",
                "name": self.rol,
                "res_model": self.modelo,
                "domain": safe_eval(self.dominio),
                "view_mode": "list,form",
                "target": "current",
            }

        raise UserError(
            _("Este componente no guarda un origen consultable. "
              "Corresponde a un valor derivado de otros componentes."))
