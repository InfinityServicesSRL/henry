# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class PurchaseOrder(models.Model):
    """Compras locales que sustituyen importaciones retenidas.

    ORIGEN: los proveedores internacionales de bobina (Vipa, SOIC, Convermat,
    Central National) retienen despachos cuando el flete oriental supera lo
    que cotizaron, porque AG Supply compra bajo condicion costo y flete. El
    patron se repite con casi todos.

    Cuando eso ocurre y no se puede esperar, hay que comprar bobina local:
    mas cara, y con terminos de contado o pocos dias de credito en lugar de
    los 90 a 120 dias del proveedor internacional.

    Eso genera tres costos que hoy no se miden:
      - Sobrecosto de material (precio local vs importado, por kilo)
      - Costo financiero (credito perdido)
      - Disrupcion de produccion, si hubo quiebre

    El tercero es el mas caro y el menos visible. Un quiebre rompe la cadena
    completa: se detienen lineas, se reprograman ordenes, se incumplen
    pedidos. Por eso se captura aparte y de forma explicita, aunque sea una
    estimacion.

    Sin este registro, el costo del problema del flete existe pero nadie sabe
    cuanto es, y por tanto no se puede negociar con numeros.
    """
    _inherit = "purchase.order"

    ags_es_sustituta = fields.Boolean(
        string="Sustituye importacion retenida",
        tracking=True,
        help="Marcar cuando esta compra local reemplaza mercancia importada "
             "que el proveedor no despacho a tiempo.",
    )
    ags_oc_sustituida_id = fields.Many2one(
        "purchase.order",
        string="Importacion afectada",
        domain="[('id','!=',id),('state','in',['purchase','done']),"
               "('ags_tiene_pendiente','=',True)]",
        help="Orden de importacion cuya demora obligo a esta compra.",
    )
    ags_motivo_sustitucion = fields.Selection(
        [
            ("flete", "Flete por encima de lo cotizado"),
            ("precio", "Aumento de precio del proveedor"),
            ("disponibilidad", "Sin disponibilidad de producto"),
            ("retencion", "Proveedor retuvo el despacho"),
            ("otro", "Otro"),
        ],
        string="Motivo",
        help="Cada motivo se negocia distinto: el flete con el naviero, la "
             "disponibilidad con el proveedor.",
    )
    ags_impacto_produccion = fields.Selection(
        [
            ("ninguno", "Ninguno, se cubrio a tiempo"),
            ("reprogramacion", "Hubo que reprogramar ordenes"),
            ("linea_detenida", "Se detuvo una linea de produccion"),
            ("incumplimiento", "Se incumplieron pedidos de clientes"),
        ],
        string="Impacto en produccion",
        help="Lo completa Gerencia, no Compras. Es el costo mas alto y el "
             "menos visible: un quiebre rompe la cadena completa.",
    )
    ags_costo_disrupcion = fields.Monetary(
        string="Costo estimado de disrupcion",
        currency_field="currency_id",
        help="Estimacion del costo de la interrupcion: paradas, "
             "reprogramacion, pedidos incumplidos. Se completa despues.",
    )

    ags_tiene_pendiente = fields.Boolean(
        string="Tiene mercancia pendiente",
        compute="_compute_tiene_pendiente",
        store=True,
        help="Usado para filtrar que importaciones pueden seleccionarse "
             "como afectadas, en lugar de mostrar cientos de ordenes.",
    )
    ags_kg_pendientes = fields.Float(
        string="Kg pendientes de recibir",
        compute="_compute_tiene_pendiente",
        store=True,
        digits=(16, 2),
    )
    ags_dias_retraso = fields.Integer(
        string="Dias de retraso",
        compute="_compute_tiene_pendiente",
        store=True,
        help="Dias transcurridos desde la fecha comprometida de recepcion",
    )

    ags_sobrecosto_material = fields.Monetary(
        string="Sobrecosto de material",
        compute="_compute_sobrecostos",
        currency_field="currency_id",
        help="Diferencia de precio por kilo entre la compra local y la "
             "importacion sustituida, por los kilos comprados.",
    )
    ags_dias_credito_perdidos = fields.Integer(
        string="Dias de credito perdidos",
        compute="_compute_sobrecostos",
    )
    ags_costo_financiero = fields.Monetary(
        string="Costo financiero",
        compute="_compute_sobrecostos",
        currency_field="currency_id",
        help="Costo de adelantar el pago: monto por dias perdidos por la "
             "tasa de costo de capital configurada.",
    )
    ags_costo_total_evento = fields.Monetary(
        string="Costo total del evento",
        compute="_compute_sobrecostos",
        currency_field="currency_id",
    )

    @api.depends("order_line.product_qty", "order_line.qty_received",
                 "date_planned", "state")
    def _compute_tiene_pendiente(self):
        hoy = fields.Date.context_today(self)
        for o in self:
            pend = 0.0
            for l in o.order_line:
                falta = (l.product_qty or 0.0) - (l.qty_received or 0.0)
                if falta > 0:
                    pend += falta * self._ags_factor_kg(l)
            o.ags_kg_pendientes = pend
            o.ags_tiene_pendiente = bool(pend > 0 and o.state in ("purchase", "done"))
            if o.date_planned and pend > 0:
                o.ags_dias_retraso = max(0, (hoy - o.date_planned.date()).days)
            else:
                o.ags_dias_retraso = 0

    @api.model
    def _ags_factor_kg(self, linea):
        """Convierte la cantidad de la linea a kilos.

        El papel en bobina se compra por kilo o tonelada, asi que el precio
        por kilo es comparable aun entre productos distintos: una bobina de
        otro gramaje o ancho sigue siendo comparable por peso.
        """
        uom = linea.product_uom
        if not uom:
            return 1.0
        nombre = (uom.name or "").lower()
        if "ton" in nombre:
            return 1000.0
        if nombre in ("kg", "kgs", "kilo", "kilos", "kilogram", "kilogramo"):
            return 1.0
        if "lb" in nombre or "libra" in nombre or "pound" in nombre:
            return 0.453592
        # Si la unidad no es de peso, se usa el peso del producto
        return linea.product_id.weight or 1.0

    def _ags_precio_por_kg(self):
        """Precio promedio ponderado por kilo de la orden."""
        self.ensure_one()
        total = kilos = 0.0
        for l in self.order_line:
            kg = (l.product_qty or 0.0) * self._ags_factor_kg(l)
            if kg <= 0:
                continue
            kilos += kg
            total += l.price_subtotal
        return (total / kilos) if kilos else 0.0, kilos

    def _ags_dias_termino(self):
        """Dias de credito del termino de pago de la orden."""
        self.ensure_one()
        term = self.payment_term_id
        if not term or not term.line_ids:
            return 0
        return max(term.line_ids.mapped("nb_days") or [0])

    @api.depends("ags_es_sustituta", "ags_oc_sustituida_id",
                 "order_line.price_subtotal", "order_line.product_qty",
                 "payment_term_id", "ags_costo_disrupcion")
    def _compute_sobrecostos(self):
        cfg = self.env["ags.config"].search([], limit=1)
        tasa = cfg.tasa_costo_capital if cfg else 12.3
        base = cfg.dias_base_anio if cfg else 365
        for o in self:
            o.ags_sobrecosto_material = 0.0
            o.ags_dias_credito_perdidos = 0
            o.ags_costo_financiero = 0.0
            o.ags_costo_total_evento = 0.0
            if not o.ags_es_sustituta or not o.ags_oc_sustituida_id:
                o.ags_costo_total_evento = o.ags_costo_disrupcion or 0.0
                continue

            orig = o.ags_oc_sustituida_id
            p_local, kilos = o._ags_precio_por_kg()
            p_import, _k = orig._ags_precio_por_kg()

            # La importacion suele estar en otra moneda: se convierte a la
            # moneda de la compra local para que la comparacion sea valida.
            if orig.currency_id != o.currency_id and p_import:
                p_import = orig.currency_id._convert(
                    p_import, o.currency_id, o.company_id,
                    o.date_order.date() if o.date_order else fields.Date.today())

            if p_local and p_import:
                o.ags_sobrecosto_material = (p_local - p_import) * kilos

            perdidos = max(0, orig._ags_dias_termino() - o._ags_dias_termino())
            o.ags_dias_credito_perdidos = perdidos
            if perdidos:
                o.ags_costo_financiero = (
                    o.amount_untaxed * (perdidos / (base or 365)) * (tasa / 100.0))

            o.ags_costo_total_evento = (
                o.ags_sobrecosto_material + o.ags_costo_financiero
                + (o.ags_costo_disrupcion or 0.0))

    @api.onchange("ags_es_sustituta")
    def _onchange_es_sustituta(self):
        if not self.ags_es_sustituta:
            self.ags_oc_sustituida_id = False
            self.ags_motivo_sustitucion = False
