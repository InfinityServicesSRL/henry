# -*- coding: utf-8 -*-
from odoo import models, fields, api

# Palabras que delatan a un acreedor financiero en el nombre del tercero.
# Se usan solo para SUGERIR la clasificacion, nunca para aplicarla sola.
PISTAS_FINANCIERO = [
    "PRESTAMO", "PRÉSTAMO", "PREST.", "COOPERATIVA", "COOP",
    "BANCO", "BANESCO", "BANRESERVAS", "SCOTIABANK", "PROMERICA",
    "ASOCIACION", "ASOCIACIÓN", "FINANCIERA", "LEASING",
]


class ResPartner(models.Model):
    """Clasificacion de terceros para el analisis de cartera.

    ORIGEN: al generar el primer aging de cuentas por pagar aparecio un 78%
    vencido, cifra que sugeria una crisis de pagos. Al revisarlo resulto que
    de los RD$ 93.5 millones, RD$ 62.3 millones eran prestamos personales,
    cooperativas y bancos -- todos figurando al 100% vencido porque un
    prestamo no tiene fecha de vencimiento de factura en sentido comercial.

    La CxP comercial real era de 31.2 millones, y los proveedores de bobina
    (Central National, Vipa, SOIC, Bridge View) estaban al 0% vencido.

    Mezclar deuda financiera con credito de proveedores en el mismo indicador
    produce un numero que asusta y no significa nada.
    """
    _inherit = "res.partner"

    ags_tipo_acreedor = fields.Selection(
        [
            ("comercial", "Proveedor comercial"),
            ("financiero", "Acreedor financiero"),
            ("acuerdo_pago", "Acuerdo de pago negociado"),
            ("relacionado", "Parte relacionada"),
            ("gubernamental", "Gubernamental"),
            ("laboral", "Personal / laboral"),
        ],
        string="Tipo de acreedor",
        help="Determina si el saldo entra al aging comercial. Los acreedores "
             "financieros se analizan aparte porque su vencimiento responde a "
             "un calendario de amortizacion, no a terminos de pago. Un acuerdo "
             "de pago negociado tampoco es cartera comercial: su calendario "
             "sustituye los terminos originales de la factura.",
    )
    ags_motivo_acuerdo = fields.Text(
        string="Motivo del acuerdo de pago",
        help="Que origino el acuerdo y en que condiciones se pacto. "
             "Documentarlo permite saber, si vuelve a ocurrir con otro "
             "proveedor, que no fue un caso aislado.",
    )
    ags_costo_incidente = fields.Monetary(
        string="Costo estimado del incidente",
        currency_field="ags_currency_id",
        help="Perdida estimada por el incumplimiento del proveedor: "
             "reproceso, atencion de reclamos, mercancia comprometida.",
    )
    ags_currency_id = fields.Many2one(
        "res.currency", string="Moneda",
        default=lambda self: self.env.company.currency_id,
    )
    ags_tipo_sugerido = fields.Char(
        string="Tipo sugerido",
        compute="_compute_tipo_sugerido",
        store=False,
        help="Deduccion a partir del nombre. Sirve de apoyo para clasificar "
             "en lote, no reemplaza la revision.",
    )

    @api.depends("name")
    def _compute_tipo_sugerido(self):
        for rec in self:
            rec.ags_tipo_sugerido = rec._ags_deducir_tipo()

    def _ags_deducir_tipo(self):
        self.ensure_one()
        nombre = (self.name or "").upper()
        if any(p in nombre for p in PISTAS_FINANCIERO):
            return "financiero"
        return False

    @api.model
    def ags_sugerir_clasificacion(self, solo_con_saldo=True):
        """Devuelve terceros sin clasificar que parecen financieros.

        No escribe nada: la clasificacion de un acreedor tiene consecuencias
        sobre los indicadores de cartera, y un nombre que contiene "banco" no
        siempre es un banco. Se propone y alguien decide.
        """
        dominio = [("ags_tipo_acreedor", "=", False)]
        candidatos = self.search(dominio)
        salida = []
        for p in candidatos:
            sug = p._ags_deducir_tipo()
            if not sug:
                continue
            saldo = 0.0
            if solo_con_saldo:
                lineas = self.env["account.move.line"].search([
                    ("partner_id", "=", p.id),
                    ("account_id.account_type", "=", "liability_payable"),
                    ("parent_state", "=", "posted"),
                    ("full_reconcile_id", "=", False),
                ])
                saldo = -sum(lineas.mapped("amount_residual"))
                if saldo <= 0:
                    continue
            salida.append({"id": p.id, "name": p.name,
                           "sugerido": sug, "saldo": saldo})
        return sorted(salida, key=lambda x: -x["saldo"])
