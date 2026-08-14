# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError

TRAMOS = [
    ("corriente", "Por vencer", 0, 0),
    ("t_0_30", "1 a 30 dias", 1, 30),
    ("t_31_60", "31 a 60 dias", 31, 60),
    ("t_61_90", "61 a 90 dias", 61, 90),
    ("t_91_180", "91 a 180 dias", 91, 180),
    ("t_180", "Mas de 180 dias", 181, 99999),
]


class AgsAging(models.Model):
    """Fotografia congelada de la antiguedad de saldos.

    POR QUE EXISTE, SI ODOO YA TRAE Aged Receivable Y Aged Payable:
    los reportes nativos muestran la foto de HOY. No guardan como estaba el
    aging hace tres meses, asi que no permiten responder si la cartera esta
    mejorando o deteriorandose.

    Este modelo congela la foto al cierre de cada periodo. Con eso se puede
    ver que el tramo de mas de 90 dias lleva tres meses creciendo, que un
    cliente paso de corriente a 60 dias, o que la proporcion corriente cae
    aunque el saldo total no cambie.

    Para la foto puntual con soporte documental -- lo que suele pedir un
    auditor -- el reporte nativo de Odoo sigue siendo la herramienta correcta.
    Este modelo no lo reemplaza.
    """
    _name = "ags.aging"
    _description = "AG Intelligence - Antiguedad de Saldos"
    _order = "fecha_corte desc, saldo_total desc"
    _rec_name = "partner_id"

    fecha_corte = fields.Date(string="Fecha de corte", required=True, index=True)
    tipo = fields.Selection(
        [("cxc", "Cuentas por cobrar"), ("cxp", "Cuentas por pagar")],
        string="Tipo", required=True, index=True,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Tercero", required=True, index=True, ondelete="cascade"
    )
    categoria_ids = fields.Many2many(
        related="partner_id.category_id", string="Categorias"
    )
    vendedor_id = fields.Many2one(
        related="partner_id.user_id", string="Responsable comercial", store=True
    )

    saldo_total = fields.Monetary(string="Saldo total", currency_field="currency_id")
    corriente = fields.Monetary(string="Por vencer", currency_field="currency_id")
    t_0_30 = fields.Monetary(string="1-30 dias", currency_field="currency_id")
    t_31_60 = fields.Monetary(string="31-60", currency_field="currency_id")
    t_61_90 = fields.Monetary(string="61-90", currency_field="currency_id")
    t_91_180 = fields.Monetary(string="91-180", currency_field="currency_id")
    t_180 = fields.Monetary(string="+180", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", string="Moneda",
        default=lambda self: self.env.company.currency_id,
    )

    vencido = fields.Monetary(
        string="Total vencido", compute="_compute_derivados", store=True,
        currency_field="currency_id",
    )
    pct_corriente = fields.Float(
        string="% corriente", compute="_compute_derivados", store=True, digits=(5, 2),
    )
    pct_mas_90 = fields.Float(
        string="% mas de 90 dias", compute="_compute_derivados", store=True,
        digits=(5, 2),
    )
    dias_promedio = fields.Float(
        string="Dias promedio ponderado", digits=(8, 1),
        help="Dias de vencimiento promedio del saldo, ponderado por monto. "
             "Negativo significa que aun no vence.",
    )
    n_documentos = fields.Integer(string="Documentos abiertos")
    limite_credito = fields.Monetary(
        string="Limite de credito", currency_field="currency_id",
    )
    excede_limite = fields.Boolean(
        string="Excede limite", compute="_compute_derivados", store=True,
    )

    @api.depends("saldo_total", "corriente", "t_91_180", "t_180", "limite_credito")
    def _compute_derivados(self):
        for r in self:
            r.vencido = r.saldo_total - r.corriente
            r.pct_corriente = (r.corriente / r.saldo_total * 100.0) if r.saldo_total else 0.0
            r.pct_mas_90 = (
                (r.t_91_180 + r.t_180) / r.saldo_total * 100.0
            ) if r.saldo_total else 0.0
            r.excede_limite = bool(
                r.limite_credito and r.saldo_total > r.limite_credito
            )

    _sql_constraints = [
        ("aging_unico", "unique(fecha_corte, tipo, partner_id)",
         "Ya existe un corte de antiguedad para ese tercero en esa fecha."),
    ]

    # ------------------------------------------------------------------
    # Generacion del corte
    # ------------------------------------------------------------------

    @api.model
    def generar_corte(self, fecha=None, tipo="cxc", recrear=False):
        """Congela la antiguedad de saldos a una fecha.

        Reconstruye el saldo A LA FECHA DE CORTE, no al momento de ejecutar:
        se descuentan solo las conciliaciones ocurridas hasta esa fecha. Sin
        eso, un corte de mayo generado en agosto mostraria la cartera ya
        cobrada y pareceria mucho mas sana de lo que estuvo.
        """
        fecha = fecha or fields.Date.context_today(self)
        tipo_cuenta = "asset_receivable" if tipo == "cxc" else "liability_payable"

        existentes = self.search([("fecha_corte", "=", fecha), ("tipo", "=", tipo)])
        if existentes:
            if not recrear:
                raise UserError(
                    _("Ya existe un corte de %(t)s al %(f)s con %(n)s registros. "
                      "Use recrear=True para reemplazarlo.")
                    % {"t": tipo, "f": fecha, "n": len(existentes)}
                )
            existentes.unlink()

        lineas = self.env["account.move.line"].search([
            ("account_id.account_type", "=", tipo_cuenta),
            ("parent_state", "=", "posted"),
            ("date", "<=", fecha),
            ("partner_id", "!=", False),
        ])

        acum = {}
        for l in lineas:
            saldo = l.debit - l.credit
            for pr in l.matched_credit_ids:
                if pr.credit_move_id.date and pr.credit_move_id.date <= fecha:
                    saldo -= pr.amount
            for pr in l.matched_debit_ids:
                if pr.debit_move_id.date and pr.debit_move_id.date <= fecha:
                    saldo += pr.amount
            if abs(saldo) < 0.01:
                continue
            if tipo == "cxp":
                saldo = -saldo
            if saldo <= 0:
                continue

            venc = l.date_maturity or l.date
            dias = (fecha - venc).days
            p = acum.setdefault(l.partner_id.id, {
                "total": 0.0, "n": 0, "pond": 0.0,
                **{k: 0.0 for k, _n, _a, _b in TRAMOS},
            })
            p["total"] += saldo
            p["n"] += 1
            p["pond"] += dias * saldo
            if dias <= 0:
                p["corriente"] += saldo
            else:
                for k, _n, a, b in TRAMOS:
                    if k != "corriente" and a <= dias <= b:
                        p[k] += saldo
                        break

        Partner = self.env["res.partner"]
        creados = []
        for pid, v in acum.items():
            partner = Partner.browse(pid)
            limite = 0.0
            if tipo == "cxc" and "credit_limit" in Partner._fields:
                limite = partner.credit_limit or 0.0
            creados.append({
                "fecha_corte": fecha, "tipo": tipo, "partner_id": pid,
                "saldo_total": v["total"], "n_documentos": v["n"],
                "dias_promedio": (v["pond"] / v["total"]) if v["total"] else 0.0,
                "limite_credito": limite,
                **{k: v[k] for k, _n, _a, _b in TRAMOS},
            })
        return self.create(creados) if creados else self.browse()

    @api.model
    def cron_generar_cortes(self):
        """Congela CxC y CxP al cierre del mes anterior."""
        hoy = fields.Date.context_today(self)
        cierre = hoy.replace(day=1) - relativedelta(days=1)
        for t in ("cxc", "cxp"):
            self.generar_corte(cierre, t, recrear=True)
        return True

    # ------------------------------------------------------------------
    # Analisis de evolucion
    # ------------------------------------------------------------------

    @api.model
    def deterioro(self, tipo="cxc", cortes=3, umbral=10.0):
        """Terceros cuyo porcentaje vencido crece de forma sostenida.

        Es lo que la foto de hoy no puede mostrar: un cliente que pasa de
        5% a 20% vencido en tres meses tiene un problema aunque su saldo
        total no haya cambiado.
        """
        fechas = self.search_read(
            [("tipo", "=", tipo)], ["fecha_corte"], order="fecha_corte desc"
        )
        unicas = sorted({f["fecha_corte"] for f in fechas}, reverse=True)[:cortes]
        if len(unicas) < 2:
            return []
        unicas = sorted(unicas)
        actual = self.search([("tipo", "=", tipo), ("fecha_corte", "=", unicas[-1])])
        previo = self.search([("tipo", "=", tipo), ("fecha_corte", "=", unicas[0])])
        prev_map = {r.partner_id.id: r for r in previo}
        salida = []
        for r in actual:
            p = prev_map.get(r.partner_id.id)
            if not p:
                continue
            pct_ant = 100.0 - p.pct_corriente
            pct_act = 100.0 - r.pct_corriente
            delta = pct_act - pct_ant
            if delta >= umbral:
                salida.append({
                    "partner": r.partner_id.display_name,
                    "saldo": r.saldo_total,
                    "pct_vencido_antes": round(pct_ant, 1),
                    "pct_vencido_ahora": round(pct_act, 1),
                    "deterioro": round(delta, 1),
                })
        return sorted(salida, key=lambda x: -x["deterioro"])
