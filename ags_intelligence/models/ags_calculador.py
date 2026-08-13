# -*- coding: utf-8 -*-
from odoo import models, fields, api


class AgsCalculador(models.AbstractModel):
    """Motor de calculo de parametros desde los datos de Odoo.

    Cada parametro declara en su campo metodo_tecnico el nombre del metodo
    que lo calcula. Los calculadores se van implementando por fase; este
    archivo crece a medida que avanzan las fases.

    Convencion: todo metodo recibe el parametro y una fecha de corte, calcula
    el valor y registra una medicion. Ninguno escribe directo en la base:
    todos pasan por _registrar() para que la trazabilidad sea uniforme.
    """
    _name = "ags.calculador"
    _description = "AG Intelligence - Motor de Calculo"

    # ------------------------------------------------------------------
    # Infraestructura comun
    # ------------------------------------------------------------------

    @api.model
    def _registrar(self, parametro, valor, fecha_periodo, origen="auto", notas=False):
        """Crea o actualiza la medicion de un parametro para un periodo."""
        Medicion = self.env["ags.medicion"]
        existente = Medicion.search([
            ("parametro_id", "=", parametro.id),
            ("fecha_periodo", "=", fecha_periodo),
        ], limit=1)
        vals = {
            "valor": valor,
            "origen": origen,
            "fecha_calculo": fields.Datetime.now(),
        }
        if notas:
            vals["notas"] = notas
        if existente:
            existente.write(vals)
            return existente
        vals.update({
            "parametro_id": parametro.id,
            "fecha_periodo": fecha_periodo,
        })
        return Medicion.create(vals)

    @api.model
    def _rango_mes(self, fecha=None):
        """Devuelve el primer y ultimo dia del mes de la fecha dada."""
        fecha = fecha or fields.Date.context_today(self)
        primero = fecha.replace(day=1)
        if fecha.month == 12:
            siguiente = fecha.replace(year=fecha.year + 1, month=1, day=1)
        else:
            siguiente = fecha.replace(month=fecha.month + 1, day=1)
        ultimo = fields.Date.subtract(siguiente, days=1)
        return primero, ultimo

    # ------------------------------------------------------------------
    # Fase 2A - Salud del ERP
    # Estos son los primeros en implementarse porque validan la calidad de
    # los datos sobre los que se apoya todo lo demas.
    # ------------------------------------------------------------------

    @api.model
    def _calc_ots_abiertas_vencidas(self, parametro):
        """Ordenes de produccion abiertas con fecha planificada ya pasada."""
        hoy = fields.Date.context_today(self)
        _, ultimo = self._rango_mes(hoy)
        cantidad = self.env["mrp.production"].search_count([
            ("state", "not in", ["done", "cancel"]),
            ("date_finished", "<", hoy),
        ])
        return self._registrar(parametro, cantidad, ultimo)

    @api.model
    def _calc_asientos_borrador(self, parametro):
        """Asientos contables en estado borrador."""
        hoy = fields.Date.context_today(self)
        _, ultimo = self._rango_mes(hoy)
        cantidad = self.env["account.move"].search_count([
            ("state", "=", "draft"),
            ("move_type", "!=", "entry"),
        ])
        return self._registrar(parametro, cantidad, ultimo)

    @api.model
    def _calc_oc_sin_confirmar(self, parametro):
        """Ordenes de compra enviadas que llevan tiempo sin confirmarse."""
        hoy = fields.Date.context_today(self)
        _, ultimo = self._rango_mes(hoy)
        limite = fields.Date.subtract(hoy, days=15)
        cantidad = self.env["purchase.order"].search_count([
            ("state", "in", ["draft", "sent"]),
            ("date_order", "<=", limite),
        ])
        return self._registrar(parametro, cantidad, ultimo)

    @api.model
    def _calc_movimientos_sin_validar(self, parametro):
        """Movimientos de inventario pendientes de validacion."""
        hoy = fields.Date.context_today(self)
        _, ultimo = self._rango_mes(hoy)
        cantidad = self.env["stock.picking"].search_count([
            ("state", "not in", ["done", "cancel"]),
            ("scheduled_date", "<", hoy),
        ])
        return self._registrar(parametro, cantidad, ultimo)

    # ------------------------------------------------------------------
    # Fase 2B - Costos y Margen
    # Pendiente de implementar. Requiere definir la estructura de costos
    # estandar por SKU y el mapeo de centros de trabajo.
    # ------------------------------------------------------------------

    # def _calc_margen_bruto(self, parametro): ...
    # def _calc_merma_conversion(self, parametro): ...
    # def _calc_costo_mp_pct_ventas(self, parametro): ...

    # ------------------------------------------------------------------
    # Fase 2C - Financiero y Caja
    # ------------------------------------------------------------------

    # def _calc_dso(self, parametro): ...
    # def _calc_dio(self, parametro): ...
    # def _calc_pct_cartera_corriente(self, parametro): ...
