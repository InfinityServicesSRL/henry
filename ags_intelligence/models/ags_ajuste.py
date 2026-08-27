# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class AgsAjuste(models.Model):
    """Exclusion declarada, temporal y auditable de cuentas de un calculo.

    POR QUE EXISTE: parte de los indicadores de AG Supply salen distorsionados
    no por la operacion sino por remanentes de la implementacion del ERP —
    cuentas puente que nunca se cerraron, bienes recibidos no facturados que
    se acumulan, transitorias de tesoreria sin conciliar. Mientras el auditor
    y contabilidad deciden el tratamiento, la gerencia necesita ver la imagen
    de la operacion sin ese ruido.

    COMO NO SE HACE: borrando el numero, cambiando la formula o metiendo la
    excepcion en el codigo del calculador. Cualquiera de las tres deja al
    indicador sin forma de explicarse, que es exactamente lo contrario de lo
    que pide una auditoria.

    COMO SI SE HACE: el valor contable crudo se sigue calculando y guardando
    igual que siempre. Ademas se calcula un segundo valor con las cuentas
    excluidas, y ambos conviven en la misma medicion. El ajuste es un
    registro con nombre, motivo, cuentas, vigencia y quien lo autorizo: no es
    una correccion, es una anotacion. Cuando el auditor decide, el ajuste se
    marca resuelto y el saneado desaparece solo.
    """
    _name = "ags.ajuste"
    _description = "AG Intelligence - Ajuste de calculo"
    _order = "fecha_desde desc, id desc"

    name = fields.Char(
        string="Ajuste", required=True,
        help="Como se le llama a esta exclusion en las conversaciones con el auditor",
    )
    motivo = fields.Text(
        string="Motivo", required=True,
        help="Por que estas cuentas distorsionan el indicador. Es lo que el "
             "auditor va a leer para decidir si el ajuste es legitimo.",
    )
    tipo = fields.Selection(
        [("excluir_cuentas", "Excluir cuentas del calculo")],
        string="Tipo", default="excluir_cuentas", required=True,
    )

    cuenta_ids = fields.Many2many(
        "account.account", string="Cuentas excluidas", required=True)
    parametro_ids = fields.Many2many(
        "ags.parametro", string="Indicadores afectados",
        help="Dejar vacio aplica el ajuste a todo indicador que toque estas "
             "cuentas. Lo recomendable es nombrarlos: un ajuste con alcance "
             "amplio es dificil de defender.",
    )

    fecha_desde = fields.Date(
        string="Vigente desde", required=True,
        default=lambda self: fields.Date.context_today(self))
    fecha_hasta = fields.Date(
        string="Vigente hasta",
        help="Vacio significa que sigue vigente. Se llena solo al resolver.")

    estado = fields.Selection(
        [
            ("borrador", "Propuesto"),
            ("vigente", "Vigente"),
            ("resuelto", "Resuelto"),
        ],
        string="Estado", default="borrador", required=True,
    )
    autorizado_por_id = fields.Many2one(
        "res.users", string="Autorizado por", readonly=True)
    fecha_autorizacion = fields.Date(string="Autorizado el", readonly=True)
    resolucion = fields.Text(
        string="Resolucion",
        help="Que decidieron finalmente el auditor y contabilidad")

    n_cuentas = fields.Integer(compute="_compute_conteos", string="Cuentas")
    n_parametros = fields.Integer(compute="_compute_conteos", string="Indicadores")
    cuentas_codigos = fields.Char(
        compute="_compute_conteos", string="Codigos")

    @api.depends("cuenta_ids", "parametro_ids")
    def _compute_conteos(self):
        for rec in self:
            rec.n_cuentas = len(rec.cuenta_ids)
            rec.n_parametros = len(rec.parametro_ids)
            rec.cuentas_codigos = ", ".join(
                sorted(c.code or "" for c in rec.cuenta_ids))

    @api.constrains("fecha_desde", "fecha_hasta")
    def _check_vigencia(self):
        for rec in self:
            if rec.fecha_hasta and rec.fecha_hasta < rec.fecha_desde:
                raise ValidationError(
                    _("La fecha de fin no puede ser anterior a la de inicio."))

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def action_activar(self):
        """Pone el ajuste en vigor y deja constancia de quien lo autorizo."""
        for rec in self:
            if not rec.cuenta_ids:
                raise ValidationError(
                    _("Un ajuste sin cuentas no excluye nada."))
            rec.write({
                "estado": "vigente",
                "autorizado_por_id": self.env.user.id,
                "fecha_autorizacion": fields.Date.context_today(self),
            })
        return True

    def action_resolver(self):
        """Cierra el ajuste: el auditor decidio y el saneado deja de aplicar."""
        hoy = fields.Date.context_today(self)
        for rec in self:
            rec.write({
                "estado": "resuelto",
                "fecha_hasta": rec.fecha_hasta or hoy,
            })
        return True

    def action_volver_borrador(self):
        self.write({"estado": "borrador"})
        return True

    def action_ver_cuentas(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cuentas excluidas por %s") % self.name,
            "res_model": "account.account",
            "domain": [("id", "in", self.cuenta_ids.ids)],
            "view_mode": "list,form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Consulta desde el calculo
    # ------------------------------------------------------------------

    @api.model
    def _vigentes(self, fecha):
        """Ajustes en vigor a una fecha de cierre."""
        return self.search([
            ("estado", "=", "vigente"),
            ("fecha_desde", "<=", fecha),
            "|", ("fecha_hasta", "=", False), ("fecha_hasta", ">=", fecha),
        ])

    @api.model
    def _aplicables(self, parametro, fecha):
        """Los vigentes que afectan a un indicador concreto.

        Un ajuste sin indicadores declarados aplica a todos: es el caso de una
        cuenta puente que ensucia cualquier ratio donde aparezca.
        """
        return self._vigentes(fecha).filtered(
            lambda a: not a.parametro_ids or parametro in a.parametro_ids)

    @api.model
    def _cuentas_excluidas(self, parametro, fecha):
        """Union de las cuentas que este indicador debe dejar fuera."""
        cuentas = self.env["account.account"]
        for ajuste in self._aplicables(parametro, fecha):
            cuentas |= ajuste.cuenta_ids
        return cuentas

    @api.model
    def _parametros_afectados(self, fecha):
        """Indicadores que hay que volver a calcular en modo saneado.

        Con ajustes de alcance abierto se recalcula todo indicador contable;
        con ajustes nombrados, solo los nombrados. La diferencia en tiempo de
        corrida es la razon practica para nombrarlos.
        """
        vigentes = self._vigentes(fecha)
        if not vigentes:
            return self.env["ags.parametro"]
        abiertos = vigentes.filtered(lambda a: not a.parametro_ids)
        if abiertos:
            return self.env["ags.parametro"].search([
                ("captura", "=", "auto"),
                ("metodo_tecnico", "!=", False),
            ])
        return vigentes.mapped("parametro_ids").filtered(
            lambda p: p.captura == "auto" and p.metodo_tecnico)
