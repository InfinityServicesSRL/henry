from odoo import models, fields


class MrpWorkorder(models.Model):
    """
    Extiende la Orden de Trabajo con el estado del incentivo de producción.

    Trazabilidad y control de pago:
      - Cada orden de trabajo nace PENDIENTE de incentivo.
      - Al confirmar el cálculo del incentivo (wizard), queda PAGADA, con la fecha,
        el período de la quincena y el/los cálculos que la incluyeron.
      - El wizard, por defecto, solo toma órdenes PENDIENTES → nunca se paga dos veces,
        y lo que no entró en una quincena queda pendiente para la siguiente.
    """
    _inherit = 'mrp.workorder'

    ag_incentivo_estado = fields.Selection(
        [('pendiente', 'Pendiente de incentivo'),
         ('pagado', 'Incentivo pagado')],
        string='Estado incentivo',
        default='pendiente',
        copy=False,
        index=True,
        help='Pendiente = aún no se ha pagado incentivo por esta orden. '
             'Pagado = ya se incluyó en un cálculo de incentivo de una quincena.',
    )
    ag_incentivo_fecha_pago = fields.Date(
        string='Fecha de pago incentivo',
        copy=False,
    )
    ag_incentivo_periodo = fields.Char(
        string='Período de incentivo',
        copy=False,
        help='Quincena en la que se pagó el incentivo de esta orden.',
    )
    ag_incentivo_calculo_ids = fields.Many2many(
        'ag.incentivo.calculo',
        'ag_incentivo_calculo_workorder_rel',
        'workorder_id',
        'calculo_id',
        string='Cálculos de incentivo',
        copy=False,
        help='Cálculos (por empleado) que repartieron el incentivo de esta orden.',
    )
