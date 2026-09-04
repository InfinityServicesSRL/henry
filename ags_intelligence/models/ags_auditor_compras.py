# -*- coding: utf-8 -*-
import logging

from dateutil.relativedelta import relativedelta
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Ventana de observacion de los habitos de registro. No es un umbral de
# tolerancia -- eso vive en regla.tolerancia -- sino cuanto pasado se mira
# para juzgar la practica actual. Treinta dias es un mes de operacion.
DIAS_VENTANA = 30


class AgsAuditorCompras(models.AbstractModel):
    """Reglas sobre el circuito de compras.

    Nacen de la caracterizacion del 31 ago 2026: la mitad de la mercancia
    entraba sin orden de compra y nueve de cada diez facturas de proveedor se
    digitaban sin referencia a una. Con eso la conciliacion entre orden,
    recepcion y factura no es que falle: no tiene con que ejecutarse, y el
    saldo de bienes recibidos no facturados solo puede crecer.

    Ese mismo dia se corrigio la POLITICA -- las 220 fichas almacenables
    pasaron a facturar por cantidad recibida -- pero la politica no arregla el
    HABITO. Una factura digitada suelta sigue sin tener contra que casar
    aunque la ficha este perfecta. Estas reglas miden el habito, que es lo
    unico que dira si el cambio pego.
    """
    _inherit = "ags.auditor"

    @api.model
    def _regla_recepcion_sin_oc(self, regla, compania):
        """Mercancia que entro sin orden de compra ni devolucion detras.

        Una recepcion suelta ingresa el inventario y acredita la cuenta de
        bienes recibidos, pero no queda ligada a nada: la factura que llegue
        despues no tiene contra que casar. Es el equivalente en compras de
        registrar una devolucion de cliente como entrada de inventario en vez
        de hacerla desde la entrega.

        Se excluyen las devoluciones (return_id) porque esas SI tienen su
        origen declarado: vienen de una entrega.
        """
        desde = fields.Date.context_today(self) - relativedelta(days=DIAS_VENTANA)
        dominio = [
            ("picking_type_id.code", "=", "incoming"),
            ("state", "=", "done"),
            ("company_id", "=", compania.id),
            ("date_done", ">=", "%s 00:00:00" % desde),
            ("purchase_id", "=", False),
            ("return_id", "=", False),
        ]
        Picking = self.env["stock.picking"]
        n = Picking.search_count(dominio)
        if not n:
            return []
        total = Picking.search_count([
            ("picking_type_id.code", "=", "incoming"),
            ("state", "=", "done"),
            ("company_id", "=", compania.id),
            ("date_done", ">=", "%s 00:00:00" % desde),
        ])
        return [{
            "clave": regla.codigo,
            "sujeto": "%s de %s recepciones de los ultimos %s dias sin orden "
                      "de compra ni devolucion" % (n, total, DIAS_VENTANA),
            "cantidad": n,
            "modelo": "stock.picking",
            "dominio": dominio,
        }]

    @api.model
    def _regla_factura_sin_oc(self, regla, compania):
        """Facturas de proveedor con mercancia y sin orden de compra.

        El filtro por producto almacenable es lo que separa el hallazgo del
        ruido: una factura de combustible o de mantenimiento sin orden de
        compra es perfectamente correcta -- es un gasto, no hay recepcion
        contra la cual casarla. Una factura de bobinas sin orden, no.
        """
        desde = fields.Date.context_today(self) - relativedelta(days=DIAS_VENTANA)
        base = [
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("company_id", "=", compania.id),
            ("invoice_date", ">=", desde),
            ("invoice_line_ids.product_id.is_storable", "=", True),
        ]
        dominio = base + [("line_ids.purchase_line_id", "=", False)]
        Factura = self.env["account.move"]
        n = Factura.search_count(dominio)
        if not n:
            return []
        total = Factura.search_count(base)
        return [{
            "clave": regla.codigo,
            "sujeto": "%s de %s facturas de proveedor con mercancia en los "
                      "ultimos %s dias, sin orden de compra"
                      % (n, total, DIAS_VENTANA),
            "cantidad": n,
            "modelo": "account.move",
            "dominio": dominio,
        }]

    @api.model
    def _regla_factura_prov_cuenta_impropia(self, regla, compania):
        """Facturas de proveedor que debitan cuentas de pasivo.

        Comprar algo debita un activo o un gasto. Nunca un pasivo: pagar un
        prestamo, distribuir dividendos o liquidar nomina no son compras, y
        registrarlos como factura de proveedor infla el volumen de compras,
        distorsiona los dias de pago y mezcla financiamiento con operacion.

        LA EXCEPCION LEGITIMA son las cuentas puente declaradas: la de bienes
        recibidos no facturados es un pasivo y debitarla es exactamente lo
        que debe hacer una factura de compra bien registrada. Por eso la
        regla se apoya en el inventario de puentes en vez de llevar su propia
        lista: una cuenta que alguien declare puente manana deja de aparecer
        aqui sin tocar codigo.

        Caso real de 2026: dividendos por 11.5 millones, prestamos bancarios
        por 10.3 y nominas por 5.75, todos registrados como factura de
        proveedor.
        """
        tipos_pasivo = ("liability_payable", "liability_current",
                        "liability_non_current")
        puentes = self.env["ags.cuenta.puente"].search([
            ("company_id", "=", compania.id)]).mapped("cuenta_id").ids

        desde = fields.Date.context_today(self) - relativedelta(days=DIAS_VENTANA)
        dominio = [
            ("move_id.move_type", "=", "in_invoice"),
            ("parent_state", "=", "posted"),
            ("company_id", "=", compania.id),
            ("date", ">=", desde),
            ("display_type", "=", "product"),
            ("account_id.account_type", "in", list(tipos_pasivo)),
            ("debit", ">", 0),
        ]
        if puentes:
            dominio.append(("account_id", "not in", puentes))

        grupos = self.env["account.move.line"]._read_group(
            dominio, ["account_id"], ["debit:sum", "__count"])
        if not grupos:
            return []

        salida = []
        for cuenta, debito, cantidad in grupos:
            nombre = self._cuentas_en_idioma(cuenta).name or cuenta.code
            salida.append({
                "clave": "%s:%s" % (regla.codigo, cuenta.id),
                "sujeto": "%s %s recibio %s en %s lineas de factura de "
                          "proveedor" % (cuenta.code, nombre,
                                         "{:,.2f}".format(debito), cantidad),
                "cantidad": cantidad,
                "modelo": "account.move.line",
                "dominio": dominio + [("account_id", "=", cuenta.id)],
            })
        return salida
