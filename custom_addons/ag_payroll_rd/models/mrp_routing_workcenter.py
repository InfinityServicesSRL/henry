from odoo import models, fields


class MrpRoutingWorkcenter(models.Model):
    """
    Extiende la Operación (mrp.routing.workcenter) con el incentivo de producción
    por unidad. Vive junto al tiempo en minutos de la operación, dentro de la
    lista de materiales (BoM). Cuando la BoM se usa en una orden de fabricación,
    la workorder apunta a esta operación y el wizard lee este valor en el momento
    del cálculo (tarifa viva: puede cambiar por tipo de papel, condición del rollo, etc.).
    """
    _inherit = 'mrp.routing.workcenter'

    ag_incentivo_unidad = fields.Float(
        string='Incentivo RD$/unidad',
        digits=(12, 4),
        help='Monto en pesos dominicanos que se paga como incentivo por cada unidad '
             'producida en esta operación. Se reparte entre los operarios según los '
             'minutos que cada uno registró en la orden. Es una tarifa viva: el cálculo '
             'de nómina toma el valor vigente al momento de correr el wizard.',
    )
