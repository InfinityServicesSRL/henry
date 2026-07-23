from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from collections import defaultdict
from datetime import datetime

_logger = logging.getLogger(__name__)


class AgIncentivoWizard(models.TransientModel):
    """
    Wizard: Cálculo de incentivo de producción para la quincena.

    Se ejecuta ANTES de confirmar el lote de nómina quincenal.
    Pasos:
      1. El usuario selecciona el período (date_from, date_to).
      2. El wizard lee todas las órdenes de fabricación CONFIRMADAS/TERMINADAS
         del período con empleados asignados.
      3. Lee el incentivo RD$/unidad desde la operación de cada workorder (tarifa viva).
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
        ('proporcional', 'Proporcional — según minutos trabajados por cada operario en la OF'),
        ('equitativo', 'Equitativo — dividir entre todos los operarios del turno'),
        ('individual', 'Individual — por empleado asignado directamente en la OF'),
    ], string='Modo de distribución', required=True, default='proporcional',
        help='Define cómo se asigna el incentivo cuando hay múltiples operarios en una OF.')

    auto_cargar_recibos = fields.Boolean(
        string='Cargar automáticamente a recibos',
        default=True,
        help='Si está activo y se seleccionó un lote, los montos se cargan al completar el wizard.',
    )
    incluir_pagadas = fields.Boolean(
        string='Incluir órdenes ya pagadas',
        default=False,
        help='Normalmente solo se toman las órdenes de trabajo con incentivo PENDIENTE. '
             'Marque esto solo si necesita recalcular incluyendo órdenes ya pagadas en una quincena anterior.',
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

        # Obtener órdenes de trabajo del período con incentivo configurado
        workorders = self._get_workorders()
        if not workorders:
            raise UserError(
                f'No se encontraron órdenes de trabajo con incentivo entre '
                f'{self.date_from} y {self.date_to}.\n'
                f'Verifique que las operaciones tengan "Incentivo RD$/unidad" configurado '
                f'y que las órdenes tengan producción registrada.'
            )

        # Calcular incentivo por empleado
        incentivos_por_empleado = self._calcular_incentivos(workorders)

        if not incentivos_por_empleado:
            raise UserError(
                'No se pudo calcular el incentivo. Verifique que:\n'
                '1. Las operaciones tienen "Incentivo RD$/unidad" configurado.\n'
                '2. Hay operarios con tiempo registrado en las órdenes de trabajo.\n'
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
                'workorder_ids': [(6, 0, list(set(data['wo_ids'])))],
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

    def _get_workorders(self):
        """
        Retorna las órdenes de trabajo (workorders) del período cuya operación
        tiene un incentivo por unidad configurado (> 0).

        El incentivo vive en la OPERACIÓN (mrp.routing.workcenter). Cada workorder
        de una OF apunta a su operación; leemos ese valor en vivo al calcular.
        """
        domain = [
            ('production_id.state', 'in', ['confirmed', 'progress', 'to_close', 'done']),
            ('date_start', '>=', str(self.date_from)),
            ('date_start', '<=', str(self.date_to) + ' 23:59:59'),
            ('qty_produced', '>', 0),
            ('operation_id.ag_incentivo_unidad', '>', 0),
        ]
        # Por defecto, solo órdenes con incentivo PENDIENTE (evita pagar dos veces).
        if not self.incluir_pagadas:
            domain.append(('ag_incentivo_estado', '=', 'pendiente'))
        return self.env['mrp.workorder'].search(domain)

    def _calcular_incentivos(self, workorders):
        """v1.6.0 - Destajo con piso diario (modelo AG Supply).

        Reparte el destajo (qty x tarifa) entre operarios segun el modo de
        distribucion, acumulandolo POR DIA. Luego, por empleado y dia:
            incentivo_dia = max(0, destajo_dia - (jornal_diario + HE_dia))
        El jornal ya se paga como sueldo fijo; el incentivo es solo el
        excedente. HE desde Asistencias (hr.attendance).
        """
        resultado = defaultdict(lambda: {'unidades': 0.0, 'monto': 0.0, 'of_ids': [], 'wo_ids': [], 'detalle': ''})
        destajo_dia = defaultdict(lambda: defaultdict(float))

        for wo in workorders:
            tarifa = wo.operation_id.ag_incentivo_unidad
            qty = wo.qty_produced
            monto_wo = qty * tarifa
            prod = wo.production_id
            etiqueta = f"{prod.name}/{wo.operation_id.name}"
            fecha_wo = (wo.date_start or wo.create_date).date()

            tiempos = self.env['mrp.workcenter.productivity'].search([
                ('workorder_id', '=', wo.id),
                ('employee_id', '!=', False),
            ])
            minutos_por_emp = defaultdict(float)
            for t in tiempos:
                minutos_por_emp[t.employee_id.id] += t.duration
            total_min = sum(minutos_por_emp.values())

            if self.modo_distribucion == 'proporcional':
                if total_min <= 0:
                    _logger.warning('WO %s sin tiempos de empleado. Omitida (proporcional).', wo.name)
                    continue
                for emp_id, mins in minutos_por_emp.items():
                    frac = mins / total_min
                    resultado[emp_id]['unidades'] += qty * frac
                    destajo_dia[emp_id][fecha_wo] += monto_wo * frac
                    if prod.id not in resultado[emp_id]['of_ids']:
                        resultado[emp_id]['of_ids'].append(prod.id)
                    resultado[emp_id]['wo_ids'].append(wo.id)
                    resultado[emp_id]['detalle'] += (
                        f"{etiqueta}: {qty:.2f} u x RD${tarifa:.4f} x {frac*100:.1f}% "
                        f"({mins:.0f} de {total_min:.0f} min) = RD${monto_wo*frac:.2f}\n"
                    )
                continue

            empleados = list(minutos_por_emp.keys())
            if not empleados:
                _logger.warning('WO %s sin operarios. Omitida.', wo.name)
                continue

            if self.modo_distribucion == 'equitativo':
                monto_por_emp = monto_wo / len(empleados)
                unidades_por_emp = qty / len(empleados)
            else:
                monto_por_emp = monto_wo
                unidades_por_emp = qty
                empleados = empleados[:1]

            for emp_id in empleados:
                resultado[emp_id]['unidades'] += unidades_por_emp
                destajo_dia[emp_id][fecha_wo] += monto_por_emp
                if prod.id not in resultado[emp_id]['of_ids']:
                    resultado[emp_id]['of_ids'].append(prod.id)
                resultado[emp_id]['wo_ids'].append(wo.id)
                resultado[emp_id]['detalle'] += (
                    f"{etiqueta}: {qty:.2f} u x RD${tarifa:.4f} = RD${monto_por_emp:.2f}\n"
                )

        for emp_id, por_dia in destajo_dia.items():
            piso_info = self._piso_diario(emp_id, sorted(por_dia.keys()))
            total_emp = 0.0
            resultado[emp_id]['detalle'] += "\n--- PISO DIARIO (jornal + HE) ---\n"
            for fecha in sorted(por_dia.keys()):
                destajo = por_dia[fecha]
                piso, he_monto = piso_info.get(fecha, (0.0, 0.0))
                inc = max(0.0, destajo - piso)
                total_emp += inc
                resultado[emp_id]['detalle'] += (
                    f"{fecha}: destajo RD${destajo:.2f} vs piso RD${piso:.2f} "
                    f"(incl. HE RD${he_monto:.2f}) -> incentivo RD${inc:.2f}\n"
                )
            resultado[emp_id]['monto'] = total_emp

        return dict(resultado)

    def _piso_diario(self, emp_id, fechas):
        """Piso del dia = jornal diario + monto de horas extras del dia.

        Jornal diario = wage mensual / 23.83 (dias laborables promedio RD).
        HE desde Asistencias: horas del dia por encima de 8, pagadas a
        (jornal/8) x (1 + overtime_pay_percent/100).
        Limitacion conocida: el corte de dia usa la fecha del check_in.
        """
        res = {}
        if not fechas:
            return res
        contrato = self.env['hr.contract'].search([
            ('employee_id', '=', emp_id),
            ('state', '=', 'open'),
        ], limit=1)
        jornal = (contrato.wage / 23.83) if contrato and contrato.wage else 0.0
        try:
            ovt = self.env['hr.rule.parameter']._get_parameter_from_code(
                'overtime_pay_percent', self.date_to)
        except Exception:
            ovt = 35.0
        factor_he = 1.0 + (ovt or 0.0) / 100.0

        asistencias = self.env['hr.attendance'].search([
            ('employee_id', '=', emp_id),
            ('check_in', '>=', datetime.combine(min(fechas), datetime.min.time())),
            ('check_in', '<=', datetime.combine(max(fechas), datetime.max.time())),
        ])
        horas_dia = defaultdict(float)
        for a in asistencias:
            if a.check_in:
                horas_dia[a.check_in.date()] += a.worked_hours or 0.0
        for f in fechas:
            he_horas = max(0.0, horas_dia.get(f, 0.0) - 8.0)
            he_monto = he_horas * (jornal / 8.0) * factor_he
            res[f] = (jornal + he_monto, he_monto)
        return res

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
                'workorder_ids': [(6, 0, line.workorder_ids.ids)],
                'notes': line.notas,
            })
            calculos_creados |= calculo

            # Cargar automáticamente al recibo si se eligió esa opción
            if self.auto_cargar_recibos and payslip:
                calculo.action_load_to_payslip()

        # ── Sellar las órdenes de trabajo como PAGADAS (trazabilidad + no repago) ──
        wos_pagadas = calculos_creados.mapped('workorder_ids')
        if wos_pagadas:
            wos_pagadas.write({
                'ag_incentivo_estado': 'pagado',
                'ag_incentivo_fecha_pago': fields.Date.today(),
                'ag_incentivo_periodo': f"{self.date_from} a {self.date_to}",
            })
            for calc in calculos_creados:
                calc.workorder_ids.write({'ag_incentivo_calculo_ids': [(4, calc.id)]})

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
    workorder_ids = fields.Many2many('mrp.workorder', string='Órdenes de trabajo incluidas')
    notas = fields.Text(string='Detalle')
    incluir = fields.Boolean(
        string='Incluir',
        default=True,
        help='Desmarcar para excluir este empleado del cálculo (ausencia, baja, etc.)',
    )
