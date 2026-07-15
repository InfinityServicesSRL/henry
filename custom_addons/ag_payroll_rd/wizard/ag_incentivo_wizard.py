from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from collections import defaultdict

_logger = logging.getLogger(__name__)


class AgIncentivoWizard(models.TransientModel):
    """
    Wizard: Cálculo de incentivo de producción para la quincena.

    Se ejecuta ANTES de confirmar el lote de nómina quincenal.
    Pasos:
      1. El usuario selecciona el período (date_from, date_to).
      2. El wizard lee todas las órdenes de fabricación CONFIRMADAS/TERMINADAS
         del período con empleados asignados.
      3. Cruza con la tabla ag.incentivo.tarifa para obtener RD$/unidad por producto.
      4. Calcula el incentivo total por empleado.
      5. Crea o actualiza registros ag.incentivo.calculo.
      6. Opcionalmente carga los montos directamente a los recibos si ya fueron generados.

    USO:
      Nómina → Lotes de nómina → [seleccionar lote] → Calcular incentivo producción
    """
    _name = 'ag.incentivo.wizard'
    _description = 'Wizard — Cálculo de incentivo de producción AG Supply'

    # ── Período ──────────────────────────────────────────────────────────────
    date_from = fields.Date(
        string='Inicio del período',
        required=True,
        default=lambda self: fields.Date.today().replace(day=1),
    )
    date_to = fields.Date(
        string='Fin del período',
        required=True,
        default=lambda self: fields.Date.today(),
    )
    payslip_batch_id = fields.Many2one(
        'hr.payslip.run',
        string='Lote de nómina',
        help='Si se selecciona, los montos calculados se cargarán automáticamente a los recibos del lote.',
    )

    # ── Configuración ─────────────────────────────────────────────────────────
    modo_distribucion = fields.Selection([
        ('equitativo', 'Equitativo — dividir entre todos los operarios del turno'),
        ('individual', 'Individual — por empleado asignado directamente en la OF'),
    ], string='Modo de distribución', required=True, default='individual',
        help='Define cómo se asigna el incentivo cuando hay múltiples operarios en una OF.')

    auto_cargar_recibos = fields.Boolean(
        string='Cargar automáticamente a recibos',
        default=True,
        help='Si está activo y se seleccionó un lote, los montos se cargan al completar el wizard.',
    )

    # ── Vista previa (readonly) ───────────────────────────────────────────────
    preview_line_ids = fields.One2many(
        'ag.incentivo.wizard.line',
        'wizard_id',
        string='Vista previa del cálculo',
        readonly=True,
    )
    total_incentivo = fields.Float(
        string='Total incentivo RD$',
        compute='_compute_total',
        readonly=True,
    )

    @api.depends('preview_line_ids.monto_incentivo')
    def _compute_total(self):
        for rec in self:
            rec.total_incentivo = sum(rec.preview_line_ids.mapped('monto_incentivo'))

    # ── Paso 1: Calcular (genera vista previa sin guardar) ───────────────────

    def action_calcular(self):
        """
        Lee las OFs del período y genera la vista previa del cálculo.
        No guarda nada todavía — el usuario revisa antes de confirmar.
        """
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError('La fecha de inicio debe ser anterior a la fecha de fin.')

        # Borrar líneas previas de esta sesión del wizard
        self.preview_line_ids.unlink()

        # Obtener órdenes de fabricación del período
        producciones = self._get_producciones()
        if not producciones:
            raise UserError(
                f'No se encontraron órdenes de fabricación confirmadas entre '
                f'{self.date_from} y {self.date_to}.'
            )

        # Calcular incentivo por empleado
        incentivos_por_empleado = self._calcular_incentivos(producciones)

        if not incentivos_por_empleado:
            raise UserError(
                'No se pudo calcular el incentivo. Verifique que:\n'
                '1. Las OFs tienen empleados asignados.\n'
                '2. Los productos tienen tarifa configurada en ag.incentivo.tarifa.\n'
                '3. Las cantidades producidas son mayores a cero.'
            )

        # Crear líneas de vista previa
        lines = []
        for employee_id, data in incentivos_por_empleado.items():
            lines.append((0, 0, {
                'wizard_id': self.id,
                'employee_id': employee_id,
                'unidades_producidas': data['unidades'],
                'monto_incentivo': data['monto'],
                'produccion_ids': [(6, 0, data['of_ids'])],
                'notas': data.get('detalle', ''),
            }))
        self.preview_line_ids = lines

        # Reabrir el wizard mostrando la vista previa
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ag.incentivo.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _get_producciones(self):
        """Retorna órdenes de fabricación confirmadas o terminadas en el período."""
        domain = [
            ('state', 'in', ['confirmed', 'progress', 'done']),
            ('date_start', '>=', str(self.date_from)),
            ('date_start', '<=', str(self.date_to) + ' 23:59:59'),
            ('qty_produced', '>', 0),
        ]
        return self.env['mrp.production'].search(domain)

    def _calcular_incentivos(self, producciones):
        """
        Cruza las OFs con las tarifas de incentivo y distribuye por empleado.

        Retorna: dict {employee_id: {'unidades': float, 'monto': float, 'of_ids': list}}
        """
        Tarifa = self.env['ag.incentivo.tarifa']
        resultado = defaultdict(lambda: {'unidades': 0.0, 'monto': 0.0, 'of_ids': [], 'detalle': ''})

        for prod in producciones:
            # Buscar tarifa vigente para este producto/centro de trabajo
            tarifa_rec = Tarifa.search([
                ('product_id', '=', prod.product_id.id),
                '|',
                ('workcenter_id', '=', False),
                ('workcenter_id', '=', prod.workcenter_id.id if prod.workcenter_id else False),
                ('date_from', '<=', str(self.date_to)),
                '|',
                ('date_to', '=', False),
                ('date_to', '>=', str(self.date_from)),
                ('active', '=', True),
            ], order='workcenter_id desc, date_from desc', limit=1)

            if not tarifa_rec:
                _logger.warning(
                    'Sin tarifa de incentivo para producto %s (OF %s). Omitida.',
                    prod.product_id.display_name, prod.name
                )
                continue

            tarifa = tarifa_rec.tarifa
            qty = prod.qty_produced
            monto_of = qty * tarifa

            # Obtener empleados de la OF
            empleados = self._get_empleados_of(prod)
            if not empleados:
                _logger.warning('OF %s sin empleados asignados. Omitida para incentivo.', prod.name)
                continue

            # Distribuir el monto de la OF entre los empleados
            if self.modo_distribucion == 'equitativo':
                monto_por_emp = monto_of / len(empleados)
                unidades_por_emp = qty / len(empleados)
            else:
                # individual: asignar el total al empleado principal (primero de la lista)
                monto_por_emp = monto_of
                unidades_por_emp = qty
                empleados = empleados[:1]

            for emp in empleados:
                resultado[emp.id]['unidades'] += unidades_por_emp
                resultado[emp.id]['monto'] += monto_por_emp
                resultado[emp.id]['of_ids'].append(prod.id)
                resultado[emp.id]['detalle'] += (
                    f"{prod.name}: {qty:.2f} u × RD${tarifa:.4f} = RD${monto_por_emp:.2f}\n"
                )

        return dict(resultado)

    def _get_empleados_of(self, produccion):
        """
        Retorna los empleados asociados a una orden de fabricación.

        Odoo 18 no tiene un campo nativo de empleado en mrp.production.
        AG Supply puede usar:
          A. El campo time_ids (hr.workcenter.productivity) que registra quién
             trabajó en la OF cuando se usa el módulo de tiempos de OF.
          B. Un campo custom 'operario_ids' añadido al módulo.
          C. Todos los empleados cuyo contrato tenga asignado ese centro de trabajo.

        Implementación actual: opción C (más robusta mientras no haya time_ids).
        Prioridad futura: opción A (una vez que se registren tiempos en planta).
        """
        # Opción A: usar tiempos registrados en la OF (si existe el módulo)
        if hasattr(produccion, 'time_ids') and produccion.time_ids:
            employees = produccion.time_ids.mapped('employee_id').filtered(lambda e: e.active)
            if employees:
                return employees

        # Opción B: campo custom 'operario_ids' si se añadió al módulo
        if hasattr(produccion, 'operario_ids') and produccion.operario_ids:
            return produccion.operario_ids

        # Opción C: contratos activos asignados a ese centro de trabajo
        if produccion.workcenter_id:
            contratos = self.env['hr.contract'].search([
                ('state', '=', 'open'),
                ('rd_aplica_incentivo', '=', True),
                ('rd_centro_trabajo_ids', 'in', produccion.workcenter_id.id),
            ])
            return contratos.mapped('employee_id')

        return self.env['hr.employee'].browse()

    # ── Paso 2: Confirmar (guarda y opcionalmente carga a recibos) ───────────

    def action_confirmar(self):
        """
        Guarda los cálculos como ag.incentivo.calculo y los carga a recibos si aplica.
        """
        self.ensure_one()
        if not self.preview_line_ids:
            raise UserError('Primero haga clic en "Calcular" para generar la vista previa.')

        calculos_creados = self.env['ag.incentivo.calculo']

        for line in self.preview_line_ids:
            # Buscar recibo del empleado en el lote (si se especificó)
            payslip = False
            if self.payslip_batch_id:
                payslip = self.payslip_batch_id.slip_ids.filtered(
                    lambda s: s.employee_id.id == line.employee_id.id
                )[:1]

            calculo = self.env['ag.incentivo.calculo'].create({
                'employee_id': line.employee_id.id,
                'date_from': self.date_from,
                'date_to': self.date_to,
                'unidades_producidas': line.unidades_producidas,
                'monto_incentivo': line.monto_incentivo,
                'payslip_id': payslip.id if payslip else False,
                'production_ids': [(6, 0, line.produccion_ids.ids)],
                'notes': line.notas,
            })
            calculos_creados |= calculo

            # Cargar automáticamente al recibo si se eligió esa opción
            if self.auto_cargar_recibos and payslip:
                calculo.action_load_to_payslip()

        # Mostrar los cálculos creados
        return {
            'type': 'ir.actions.act_window',
            'name': 'Incentivos calculados',
            'res_model': 'ag.incentivo.calculo',
            'domain': [('id', 'in', calculos_creados.ids)],
            'view_mode': 'list,form',
            'target': 'current',
        }


class AgIncentivoWizardLine(models.TransientModel):
    """Línea de vista previa en el wizard de incentivo."""
    _name = 'ag.incentivo.wizard.line'
    _description = 'Línea de vista previa — Incentivo de producción'
    _order = 'monto_incentivo desc'

    wizard_id = fields.Many2one('ag.incentivo.wizard', required=True, ondelete='cascade')
    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True)
    department_id = fields.Many2one(related='employee_id.department_id', string='Departamento')
    unidades_producidas = fields.Float(string='Unidades producidas', digits=(12, 2))
    monto_incentivo = fields.Float(string='Incentivo RD$', digits=(12, 2))
    produccion_ids = fields.Many2many('mrp.production', string='Órdenes incluidas')
    notas = fields.Text(string='Detalle')
    incluir = fields.Boolean(
        string='Incluir',
        default=True,
        help='Desmarcar para excluir este empleado del cálculo (ausencia, baja, etc.)',
    )
