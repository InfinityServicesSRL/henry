from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class AgIncentivoCalculo(models.Model):
    """
    Resultado del cálculo de incentivo por empleado y período.

    Se genera al ejecutar el wizard antes del cierre de quincena y
    se carga como 'otra entrada' (hr.payslip.input) en el recibo.
    """
    _name = 'ag.incentivo.calculo'
    _description = 'Cálculo de incentivo de producción por quincena (AG Supply)'
    _order = 'date_from desc, employee_id'

    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True)
    date_from = fields.Date(string='Inicio período', required=True)
    date_to = fields.Date(string='Fin período', required=True)
    unidades_producidas = fields.Float(string='Unidades producidas', digits=(12, 2))
    tarifa_promedio = fields.Float(string='Tarifa promedio aplicada', digits=(12, 4))
    monto_incentivo = fields.Float(string='Incentivo RD$', digits=(12, 2))
    payslip_id = fields.Many2one('hr.payslip', string='Recibo de nómina')
    state = fields.Selection([
        ('draft', 'Calculado'),
        ('loaded', 'Cargado al recibo'),
        ('paid', 'Pagado'),
    ], default='draft', string='Estado')
    production_ids = fields.Many2many(
        'mrp.production',
        string='Órdenes de fabricación incluidas',
        help='Órdenes que contribuyeron a este cálculo',
    )
    workorder_ids = fields.Many2many(
        'mrp.workorder',
        'ag_incentivo_calculo_workorder_rel',
        'calculo_id',
        'workorder_id',
        string='Órdenes de trabajo incluidas',
        help='Órdenes de trabajo (operaciones) cuyo incentivo se repartió en este cálculo',
    )
    notes = fields.Text(string='Detalle del cálculo')

    def action_load_to_payslip(self):
        """Carga el monto calculado como 'otra entrada' en el recibo del empleado."""
        InputType = self.env['hr.payslip.input.type']
        input_type = InputType.search([('code', '=', 'INCPROD')], limit=1)
        if not input_type:
            raise UserError('No se encontró el tipo de entrada INCPROD. Verificar configuración del módulo.')

        for rec in self:
            if not rec.payslip_id:
                raise UserError(f'El cálculo de {rec.employee_id.name} no tiene recibo asignado.')

            existing = rec.payslip_id.input_line_ids.filtered(
                lambda l: l.input_type_id.code == 'INCPROD'
            )
            if existing:
                existing.write({'amount': rec.monto_incentivo})
            else:
                rec.payslip_id.input_line_ids.create({
                    'payslip_id': rec.payslip_id.id,
                    'input_type_id': input_type.id,
                    'amount': rec.monto_incentivo,
                    'name': f'Incentivo producción {rec.date_from} – {rec.date_to}',
                })
            rec.write({'state': 'loaded', 'payslip_id': rec.payslip_id.id})
            _logger.info(
                'Incentivo INCPROD RD$ %.2f cargado al recibo %s del empleado %s',
                rec.monto_incentivo, rec.payslip_id.name, rec.employee_id.name,
            )
