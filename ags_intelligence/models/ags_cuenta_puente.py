# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Tolerancia sugerida al DESCUBRIR una cuenta, en dias. No es un umbral de
# calculo: es una semilla razonable para que el inventario no nazca con todo
# en cero y dispare mil hallazgos el primer dia. El valor que manda es el del
# registro, editable en la interfaz (mismo criterio que D1).
TOLERANCIA_SUGERIDA = {
    "cobros": 5,
    "pagos": 5,
    "conciliacion": 5,
    "transferencias": 2,
    "recepcion": 45,
    "envio": 30,
    "nomina": 10,
    "impuestos": 45,
    "otro": 30,
}


class AgsCuentaPuente(models.Model):
    """Inventario declarado de cuentas puente.

    QUE ES UNA CUENTA PUENTE. Una cuenta que se debita o se acredita como
    paso intermedio de un circuito y que, cuando la secuencia termina,
    vuelve a cero. Su proposito no es acumular: es sostener un importe
    mientras el proceso llega a su otro extremo. Recibos pendientes, pagos
    pendientes, transferencia de liquidez, bienes recibidos no facturados,
    bienes enviados no facturados.

    POR QUE EL SALDO ES LA METRICA EQUIVOCADA. Una cuenta puente que mueve
    veintiocho millones y los liquida cada semana esta sana. Lo que la define
    no es cuanto tiene, sino SI OSCILA O SI ACUMULA. Un saldo que solo sube
    desde 2025 es un circuito que nunca cerro, y eso no se ve mirando la
    cifra del mes: se ve mirando si alguna vez cruzo cero.

    De ahi que este modelo no guarde saldos. Guarda lo que hace falta para
    juzgarlos: que proceso usa cada cuenta, cuantos dias es razonable que una
    partida espere ahi, y en cuantos meses deberia haber cruzado cero al
    menos una vez.
    """
    _name = "ags.cuenta.puente"
    _description = "AG Intelligence - Cuenta puente"
    _order = "proceso, id"
    _rec_name = "cuenta_id"

    cuenta_id = fields.Many2one(
        "account.account", string="Cuenta", required=True,
        ondelete="cascade", index=True)
    company_id = fields.Many2one(
        "res.company", string="Compania", required=True,
        default=lambda self: self.env.company, index=True)

    proceso = fields.Selection(
        [
            ("cobros", "Cobros pendientes de conciliar"),
            ("pagos", "Pagos pendientes de conciliar"),
            ("conciliacion", "Conciliacion bancaria (transitoria de diario)"),
            ("transferencias", "Transferencias entre cuentas propias"),
            ("recepcion", "Mercancia recibida no facturada"),
            ("envio", "Mercancia enviada no facturada"),
            ("nomina", "Nomina por liquidar"),
            ("impuestos", "Impuestos por liquidar"),
            ("otro", "Otro"),
        ],
        string="Proceso que la usa", required=True, default="otro",
        help="Determina que se le exige. Una transferencia entre bancos "
             "propios deberia cerrar el mismo dia; una recepcion de mercancia "
             "puede esperar semanas a su factura.",
    )
    dias_tolerancia = fields.Integer(
        string="Dias de tolerancia", default=30, required=True,
        help="Cuantos dias es razonable que una partida espere aqui antes de "
             "considerarse atascada. Vive en el registro y no en el codigo, "
             "por la misma razon que los umbrales de los indicadores viven en "
             "el benchmark.",
    )
    meses_ciclo = fields.Integer(
        string="Meses para cruzar cero", default=3, required=True,
        help="En cuantos meses esta cuenta deberia haber cruzado cero al "
             "menos una vez. Si en toda la ventana el saldo mantuvo el mismo "
             "signo sin acercarse a cero, el circuito no esta cerrando.",
    )
    cierre_esperado = fields.Selection(
        [
            ("cero", "Cero"),
            ("deudor", "Saldo deudor"),
            ("acreedor", "Saldo acreedor"),
        ],
        string="Cierre esperado", default="cero", required=True,
        help="Casi siempre cero. Las excepciones hay que poder nombrarlas.",
    )
    responsable_id = fields.Many2one("res.users", string="Responsable")
    origen = fields.Selection(
        [
            ("descubierta", "Descubierta desde la configuracion"),
            ("declarada", "Declarada a mano"),
        ],
        string="Origen", default="declarada", required=True, readonly=True,
    )
    activa = fields.Boolean(string="Activa", default=True)
    notas = fields.Text(string="Notas")

    saldo = fields.Float(
        string="Saldo actual", compute="_compute_situacion", digits=(16, 2))
    partidas_abiertas = fields.Integer(
        string="Partidas sin conciliar", compute="_compute_situacion")

    _sql_constraints = [
        ("puente_unico", "unique(cuenta_id, company_id)",
         "Esa cuenta ya esta declarada como puente en la compania."),
    ]

    @api.constrains("dias_tolerancia", "meses_ciclo")
    def _check_positivos(self):
        for rec in self:
            if rec.dias_tolerancia < 0 or rec.meses_ciclo < 1:
                raise ValidationError(_(
                    "Los dias de tolerancia no pueden ser negativos y la "
                    "ventana debe ser de al menos un mes."))

    @api.depends("cuenta_id", "company_id")
    def _compute_situacion(self):
        """Saldo y partidas abiertas, en dos consultas para todo el conjunto.

        Se resuelve por lotes y no cuenta a cuenta: son pocas cuentas pero se
        leen en cada listado, y una consulta por fila es como se degradan
        estas pantallas sin que nadie lo note hasta que hay cincuenta.
        """
        self.saldo = 0.0
        self.partidas_abiertas = 0
        validos = self.filtered(lambda r: r.cuenta_id)
        if not validos:
            return
        Linea = self.env["account.move.line"]
        base = [("account_id", "in", validos.mapped("cuenta_id").ids),
                ("parent_state", "=", "posted")]

        saldos = {c.id: s for c, s in Linea._read_group(
            base, ["account_id"], ["balance:sum"])}
        abiertas = {c.id: n for c, n in Linea._read_group(
            base + [("full_reconcile_id", "=", False)],
            ["account_id"], ["__count"])}
        for rec in validos:
            rec.saldo = saldos.get(rec.cuenta_id.id, 0.0)
            rec.partidas_abiertas = abiertas.get(rec.cuenta_id.id, 0)

    @api.depends("cuenta_id", "proceso")
    def _compute_display_name(self):
        etiquetas = dict(self._fields["proceso"].selection)
        for rec in self:
            rec.display_name = "%s · %s" % (
                rec.cuenta_id.display_name or "", etiquetas.get(rec.proceso, ""))

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------

    @api.model
    def descubrir(self, company=None):
        """Prepobla el inventario desde la configuracion real de Odoo.

        Las cuentas puente no se listan a mano: Odoo ya sabe cuales son
        porque es el quien las usa. Cada metodo de pago declara su cuenta
        pendiente, cada diario su transitoria, la compania su cuenta de
        transferencias entre bancos, y cada categoria de producto sus cuentas
        de entrada y salida de mercancia.

        Descubrirlas en vez de escribirlas es lo que hace que el inventario
        siga siendo cierto cuando alguien agregue un banco el mes que viene.
        Es el mismo criterio que ya usa ags_calculador_calidad.
        """
        company = company or self.env.company
        propuestas = {}

        def anotar(cuenta, proceso):
            if cuenta and cuenta.id not in propuestas:
                propuestas[cuenta.id] = proceso

        for linea in self.env["account.payment.method.line"].search([]):
            if not linea.payment_account_id:
                continue
            tipo = linea.payment_method_id.payment_type
            anotar(linea.payment_account_id,
                   "cobros" if tipo == "inbound" else "pagos")

        for diario in self.env["account.journal"].search(
                [("type", "in", ["bank", "cash"])]):
            anotar(diario.suspense_account_id, "conciliacion")

        # La cuenta de transferencias entre bancos propios es el caso mas
        # puro: cada traspaso tiene dos extremos y el saldo tiene que volver
        # a cero el mismo dia. Un saldo vivo ahi son traspasos con un solo
        # extremo registrado.
        if "transfer_account_id" in self.env["res.company"]._fields:
            anotar(company.transfer_account_id, "transferencias")

        categorias = self.env["product.category"].search([])
        for categoria in categorias:
            anotar(categoria.property_stock_account_input_categ_id, "recepcion")
            anotar(categoria.property_stock_account_output_categ_id, "envio")

        existentes = set(self.search([
            ("company_id", "=", company.id)]).mapped("cuenta_id").ids)
        nuevas = []
        for cuenta_id, proceso in propuestas.items():
            if cuenta_id in existentes:
                continue
            nuevas.append({
                "cuenta_id": cuenta_id,
                "company_id": company.id,
                "proceso": proceso,
                "dias_tolerancia": TOLERANCIA_SUGERIDA.get(proceso, 30),
                "origen": "descubierta",
            })
        creadas = self.create(nuevas) if nuevas else self.browse()
        _logger.info(
            "ags.cuenta.puente: %s descubiertas, %s ya declaradas (%s)",
            len(creadas), len(existentes), company.name)
        return creadas

    @api.model
    def cuentas_candidatas(self, company=None):
        """Las cuentas que Odoo usa como puente, esten declaradas o no.

        La usa la regla PUENTE_SIN_DECLARAR: sirve para comparar lo que el
        ERP hace contra lo que el inventario dice.
        """
        company = company or self.env.company
        antes = self.search([("company_id", "=", company.id)])
        cuentas = self.env["account.account"].browse()

        for linea in self.env["account.payment.method.line"].search([]):
            cuentas |= linea.payment_account_id
        for diario in self.env["account.journal"].search(
                [("type", "in", ["bank", "cash"])]):
            cuentas |= diario.suspense_account_id
        if "transfer_account_id" in self.env["res.company"]._fields:
            cuentas |= company.transfer_account_id
        categorias = self.env["product.category"].search([])
        cuentas |= categorias.mapped("property_stock_account_input_categ_id")
        cuentas |= categorias.mapped("property_stock_account_output_categ_id")

        return cuentas.filtered(lambda c: c), antes

    def action_ver_partidas(self):
        """Los apuntes vivos de esta cuenta, con el mismo criterio que se
        uso para juzgarla."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Partidas sin conciliar de %s") % self.cuenta_id.display_name,
            "res_model": "account.move.line",
            "domain": [
                ("account_id", "=", self.cuenta_id.id),
                ("parent_state", "=", "posted"),
                ("full_reconcile_id", "=", False),
            ],
            "view_mode": "list,form",
            "context": {"search_default_group_by_partner": 1,
                        "allowed_company_ids": [self.company_id.id]},
            "target": "current",
        }
