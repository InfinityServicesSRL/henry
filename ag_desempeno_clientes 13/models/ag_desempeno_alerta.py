# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AgDesempenoAlerta(models.Model):
    """Una condición accionable detectada sobre un cliente.

    Las alertas son independientes del score a propósito: un cliente grado A
    puede tener una factura vencida, y un grado D puede no requerir nada esta
    semana. El score ordena la cartera; las alertas dicen qué hacer hoy.
    """
    _name = "ag.desempeno.alerta"
    _description = "Alerta de desempeño de cliente"
    _order = "severidad desc, fecha desc, id desc"
    _rec_name = "titulo"

    TIPOS = [
        ("credito_excedido", "Crédito excedido"),
        ("deterioro_combinado", "Deterioro combinado"),
        ("deterioro", "Caída de volumen"),
        ("dormido", "Cliente dormido"),
        ("margen_erosionado", "Margen erosionado"),
        ("backlog_estancado", "Backlog estancado"),
        ("sin_facturar", "Entregado sin facturar"),
        ("vencido_critico", "Vencido crítico"),
        ("concentracion", "Concentración de riesgo"),
        ("costeo_dudoso", "Margen fuera de rango — revisar costeo"),
        ("uso_credito", "Uso alto de crédito"),
        ("brecha_potencial", "Brecha contra el potencial"),
        ("logistica_come_margen", "La entrega se come el margen"),
    ]

    partner_id = fields.Many2one(
        "res.partner", string="Cliente", required=True, index=True,
        ondelete="cascade")
    company_id = fields.Many2one(
        "res.company", string="Compañía", required=True,
        default=lambda self: self.env.company)
    tipo = fields.Selection(TIPOS, string="Tipo", required=True, index=True)
    severidad = fields.Selection(
        [("info", "Informativa"), ("warn", "Vigilar"), ("crit", "Actuar")],
        string="Severidad", required=True, default="warn", index=True)
    titulo = fields.Char(string="Título", required=True)
    detalle = fields.Text(string="Detalle")
    valor = fields.Float(string="Valor", digits=(16, 2))
    fecha = fields.Date(string="Detectada", default=fields.Date.context_today, index=True)
    activa = fields.Boolean(string="Activa", default=True, index=True)
    user_id = fields.Many2one("res.users", string="Vendedor")
    atendida = fields.Boolean(string="Atendida", default=False)
    atendida_por = fields.Many2one("res.users", string="Atendida por", readonly=True)
    atendida_el = fields.Datetime(string="Atendida el", readonly=True)
    nota_seguimiento = fields.Text(string="Nota de seguimiento")

    def action_marcar_atendida(self):
        self.write({
            "atendida": True,
            "atendida_por": self.env.user.id,
            "atendida_el": fields.Datetime.now(),
        })
        return True

    def action_reabrir(self):
        self.write({"atendida": False, "atendida_por": False, "atendida_el": False})
        return True

    @api.model
    def sembrar(self, partner, tipo, severidad, titulo, detalle=None, valor=0.0):
        """Crea o actualiza la alerta viva de ese tipo para ese cliente.

        No duplica: si la condición sigue presente en la siguiente corrida se
        refresca el texto y la fecha, conservando la nota de seguimiento que
        haya escrito el vendedor.
        """
        existente = self.search([
            ("partner_id", "=", partner.id),
            ("tipo", "=", tipo),
            ("activa", "=", True),
            ("company_id", "=", partner.company_id.id or self.env.company.id),
        ], limit=1)
        vals = {
            "severidad": severidad,
            "titulo": titulo,
            "detalle": detalle or "",
            "valor": valor,
            "fecha": fields.Date.context_today(self),
            "user_id": partner.user_id.id or False,
        }
        if existente:
            existente.write(vals)
            return existente
        vals.update({
            "partner_id": partner.id,
            "tipo": tipo,
            "company_id": partner.company_id.id or self.env.company.id,
        })
        return self.create(vals)
