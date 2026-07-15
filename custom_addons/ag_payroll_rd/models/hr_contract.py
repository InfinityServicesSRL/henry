from odoo import models, fields


class HrContract(models.Model):
    """
    Extensión del contrato para datos específicos de nómina dominicana.
    """
    _inherit = 'hr.contract'

    # ─── TSS ──────────────────────────────────────────────────────────────────
    rd_afp_id = fields.Many2one(
        'res.partner',
        string='AFP',
        domain=[('is_company', '=', True)],
        help='Administradora de Fondos de Pensiones del empleado (ej. Siembra, Popular, Reservas)',
    )
    rd_ars_id = fields.Many2one(
        'res.partner',
        string='ARS',
        domain=[('is_company', '=', True)],
        help='Administradora de Riesgos de Salud del empleado (ej. Humano, Universal, Senasa)',
    )
    rd_nss = fields.Char(
        string='NSS',
        help='Número de Seguridad Social del empleado en la TSS',
        size=20,
    )

    # ─── Información de pago ──────────────────────────────────────────────────
    rd_banco_id = fields.Many2one(
        'res.partner',
        string='Banco de nómina',
        domain=[('is_company', '=', True)],
        help='Banco donde el empleado recibe su salario',
    )
    rd_cuenta_bancaria = fields.Char(
        string='Cuenta bancaria',
        size=30,
    )

    # ─── Incentivo de producción ───────────────────────────────────────────────
    rd_aplica_incentivo = fields.Boolean(
        string='Aplica incentivo de producción',
        default=False,
        help='Activa el cálculo de incentivo por unidades producidas en las quincenas',
    )
    rd_centro_trabajo_ids = fields.Many2many(
        'mrp.workcenter',
        string='Centros de trabajo',
        help='Centros de trabajo donde se contabilizan las unidades para el incentivo',
    )

    # ─── Pasivo laboral ───────────────────────────────────────────────────────
    rd_fecha_ingreso_real = fields.Date(
        string='Fecha de ingreso real',
        help='Fecha de inicio real para cálculo de antigüedad, preaviso y cesantía. '
             'Puede diferir de date_start si hubo período probatorio no contabilizado.',
    )
