# -*- coding: utf-8 -*-
import re
from odoo import models, fields, api, _

# Patrones detectados en el historico real de AG Supply (feb-jul 2026, 155 notas).
# El orden IMPORTA: se evalua de arriba abajo y gana el primero que coincide.
# Los mas especificos van primero para que no los absorba un patron general.
PATRONES = [
    # --- Descuento financiero -----------------------------------------
    ("pronto_pago", [
        r"PRONTO\s*PAGO",
        r"\bDESC\.?\s*\d+(\.\d+)?\s*%",
        r"\bDEC\.?\s*PRONTO",
        r"DESCUENTO\s*\d+(\.\d+)?\s*%",
    ]),
    # --- Error de precio ----------------------------------------------
    # 25 casos en el semestre: uno de cada seis documentos sale mal
    # facturado de precio. Merece categoria propia, no es devolucion.
    ("error_precio", [
        r"DIFERENCIA\s*(EN\s*)?PRE",
        r"DIFERENCIA\s*PRTECI",          # error de tipeo frecuente
        r"PRECIO\s*MAL",
        r"ARREGLAR\s*PRECIO",
        r"MAL\s*FACTURAD",
        r"NO\s*C[OM]NTEMPLO\s*ITBI",   # ITBIS omitido
        r"NO\s*COMTENPLO",
    ]),
    # --- Consumo interno facturado por error --------------------------
    ("consumo_interno", [
        r"SALIO\s*(DE\s*)?CONSUMO",
        r"CONSUMO\s*INTERNO",
    ]),
    # --- Datos fiscales incorrectos -----------------------------------
    ("datos_fiscales", [
        r"\bRNC\b.*INCORREC",
        r"\bRNC\b.*(MAL|ERRAD)",
        r"COMPROBANTE\s*EX",
        r"ERA\s*COMPROBANTE",
        r"NCF\s*(MAL|INCORRECT)",
    ]),
    # --- Refacturacion administrativa (e-CF no admite modificar) ------
    ("refacturado", [
        r"SE\s*HIZO\s*DE\s*NUEVO",
        r"SE\s*HAR[AÁ]\s*DE\s*NUEVO",
        r"SE\s*REALIZAR[AÁ]\s*DE\s*N",
        r"FACTURAD[OA]\s*POR\s*ERR",
        r"POR\s*ERR",
        r"CORRECC",
        r"MONEDA\s*ESTA\s*MA",
        r"FUE\s*FACTURADO\s*A\s*C",
    ]),
    # --- Rechazo en entrega -------------------------------------------
    # Patron dominante entre las devoluciones: mercancia que no se pudo
    # entregar. Apunta a coordinacion de despacho, no a calidad.
    ("rechazo_entrega", [
        r"(CLIENTE|CTE)\s*NO\s*RECIBI",
        r"NO\s*RECIBI[OÓ]",
        r"NO\s*SE\s*ENTREG",
        r"(CLIENTE|CTE)\s*NO\s*QUISO",
        r"NO\s*LE\s*LLEG",
        r"RECHAZ",
        r"DEV\.?\s*NO\s*RECIBIO",
    ]),
    # --- Producto equivocado o defectuoso -----------------------------
    ("producto_incorrecto", [
        r"MERCANCIA\s*DEFECTU",
        r"DEFECTU",
        r"PRODUCTO\s*EQUIVOCA",
        r"NO\s*ERA\s*(ESTE|EL)",
        r"NO\s*ERA\b",
        r"NOERA",
        r"NO\s*VAN\b",
        r"FALTO\s*UN",
        r"DEVOLUCION\s*POR\s*CO",
    ]),
    # --- Devolucion generica (ultimo recurso) -------------------------
    ("devolucion", [
        r"DEVOLUC",
        r"\bDEVL?\.",
        r"\bDEV\b",
    ]),
]


class AccountMove(models.Model):
    """Motivo de la nota de credito.

    HALLAZGO QUE ORIGINA ESTE CAMPO: en el analisis de feb-jul 2026 aparecieron
    RD$ 2.28 millones en notas de credito, que a primera vista parecian
    devoluciones. Al revisarlas una por una, la mayoria eran descuentos por
    pronto pago registrados como NC.

    Mezclar ambas cosas hace el indicador inaccionable: si el numero sube, no
    se sabe si se vendio mas al contado o si la planta despacho mal. Son dos
    problemas distintos con remedios distintos.

    Ademas, en factura electronica (e-CF) una factura emitida no se puede
    modificar: hay que anular con NC completa y rehacerla. Esas anulaciones
    administrativas no son devoluciones y no deben contaminar el indicador.
    """
    _inherit = "account.move"

    ags_motivo_nc = fields.Selection(
        [
            ("pronto_pago", "Descuento por pronto pago"),
            ("rechazo_entrega", "Rechazo en entrega (cliente no recibio)"),
            ("producto_incorrecto", "Producto equivocado o defectuoso"),
            ("error_precio", "Error de precio o facturacion"),
            ("refacturado", "Refacturacion por e-CF"),
            ("consumo_interno", "Consumo interno facturado por error"),
            ("datos_fiscales", "Datos fiscales incorrectos (RNC/NCF)"),
            ("devolucion", "Devolucion sin causa especificada"),
            ("acuerdo", "Acuerdo comercial"),
            ("otro", "Otro"),
        ],
        string="Motivo de la NC",
        tracking=True,
        help="Clasificar el motivo permite separar el costo financiero del "
             "pronto pago de la falla operativa de una devolucion.",
    )
    ags_motivo_sugerido = fields.Char(
        string="Motivo sugerido",
        compute="_compute_motivo_sugerido",
        store=False,
        help="Deduccion automatica a partir del texto de la referencia. "
             "Sirve de apoyo, no reemplaza la clasificacion manual.",
    )
    ags_dias_hasta_nc = fields.Integer(
        string="Dias desde la factura",
        compute="_compute_dias_hasta_nc",
        store=True,
        help="Dias entre la factura original y la nota de credito. Un valor "
             "bajo sugiere correccion administrativa; uno alto, devolucion real.",
    )

    # Motivos que representan falla operativa: cuestan dinero y son evitables
    MOTIVOS_DEVOLUCION = [
        "rechazo_entrega", "producto_incorrecto", "devolucion",
    ]
    # Motivos que son correccion administrativa: no reflejan falla de producto
    MOTIVOS_ADMINISTRATIVOS = [
        "refacturado", "error_precio", "consumo_interno", "datos_fiscales",
    ]

    @api.depends("ref", "narration")
    def _compute_motivo_sugerido(self):
        for rec in self:
            rec.ags_motivo_sugerido = (
                rec._ags_deducir_motivo() if rec.move_type == "out_refund" else False
            )

    @api.depends("invoice_date", "reversed_entry_id.invoice_date")
    def _compute_dias_hasta_nc(self):
        for rec in self:
            orig = rec.reversed_entry_id
            if (rec.move_type == "out_refund" and orig
                    and orig.invoice_date and rec.invoice_date):
                rec.ags_dias_hasta_nc = (rec.invoice_date - orig.invoice_date).days
            else:
                rec.ags_dias_hasta_nc = 0

    def _ags_deducir_motivo(self):
        """Deduce el motivo desde el texto libre de la referencia."""
        self.ensure_one()
        texto = "%s %s" % (self.ref or "", self.narration or "")
        texto = texto.upper()
        if not texto.strip():
            return False
        for motivo, patrones in PATRONES:
            for pat in patrones:
                if re.search(pat, texto):
                    return motivo
        return False

    @api.model
    def ags_clasificar_historico(self, desde=None, hasta=None, sobrescribir=False):
        """Clasifica notas de credito historicas segun el texto de referencia.

        Se ejecuta una sola vez sobre el historico. Las que no se puedan
        deducir quedan SIN clasificar a proposito: es preferible un vacio
        visible a una clasificacion inventada que despues nadie cuestiona.
        """
        dominio = [("move_type", "=", "out_refund"), ("state", "=", "posted")]
        if desde:
            dominio.append(("invoice_date", ">=", desde))
        if hasta:
            dominio.append(("invoice_date", "<", hasta))
        if not sobrescribir:
            dominio.append(("ags_motivo_nc", "=", False))

        ncs = self.search(dominio)
        resultado = {"clasificadas": 0, "sin_deducir": 0, "por_motivo": {}}
        for nc in ncs:
            motivo = nc._ags_deducir_motivo()
            if motivo:
                nc.ags_motivo_nc = motivo
                resultado["clasificadas"] += 1
                resultado["por_motivo"][motivo] = resultado["por_motivo"].get(motivo, 0) + 1
            else:
                resultado["sin_deducir"] += 1
        # El shell de Odoo no confirma la transaccion al salir: sin este
        # commit la clasificacion se pierde al cerrar la sesion.
        self.env.cr.commit()
        return resultado

    def action_ags_clasificar(self):
        """Boton para clasificar el historico desde la interfaz."""
        res = self.ags_clasificar_historico()
        detalle = "\n".join(
            "  %s: %s" % (k, v) for k, v in sorted(res["por_motivo"].items()))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Clasificacion de notas de credito"),
                "message": _("Clasificadas: %(c)s\nSin deducir: %(s)s\n\n%(d)s") % {
                    "c": res["clasificadas"], "s": res["sin_deducir"], "d": detalle},
                "type": "success",
                "sticky": True,
            },
        }
