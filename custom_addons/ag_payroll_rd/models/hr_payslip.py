from odoo import models, fields, api
from odoo.exceptions import UserError


class HrPayslip(models.Model):
    """
    Extensión del recibo de nómina para cálculo ISR dominicano anualizado.

    El ISR (DGII) requiere anualizar el salario mensual, aplicar la escala vigente
    y dividir entre 24 para obtener la retención por quincena.
    """
    _inherit = 'hr.payslip'

    # ─── Campos de auditoría ISR ──────────────────────────────────────────────
    rd_base_isr_anual = fields.Float(
        string='Base ISR anualizada (RD$)',
        help='Salario anual bruto menos TSS empleado anualizado. Base para escala DGII.',
        readonly=True,
    )
    rd_isr_anual = fields.Float(
        string='ISR anual calculado (RD$)',
        help='ISR según escala vigente antes de dividir entre 24.',
        readonly=True,
    )
    rd_isr_quincenal = fields.Float(
        string='Retención ISR quincenal (RD$)',
        help='ISR anual / 24. Este valor va al recibo.',
        readonly=True,
    )

    # ─── Lógica de cálculo ISR ────────────────────────────────────────────────

    def _get_rd_isr(self, bruto_quincena, sfs_emp_quincena, afp_emp_quincena):
        """
        Calcula la retención ISR quincenal según la legislación dominicana 2026.

        Parámetros
        ----------
        bruto_quincena : float
            Salario bruto del período (quincena), incluyendo incentivos y horas extra gravables.
        sfs_emp_quincena : float
            Descuento SFS al empleado en la quincena.
        afp_emp_quincena : float
            Descuento AFP al empleado en la quincena.

        Retorna
        -------
        float
            Retención ISR a descontar en este recibo.

        Nota: la regalía pascual está EXENTA de ISR hasta 1/12 del salario ordinario anual.
        Las comisiones e incentivos SÍ son gravables.
        """
        # Obtener parámetros de la DGII desde hr.rule.parameter (actualizables sin cambiar código)
        param = self.env['hr.rule.parameter']
        date = self.date_from or fields.Date.today()

        exencion   = param._get_parameter_value('rd_isr_exencion', date)
        t2_techo   = param._get_parameter_value('rd_isr_t2_techo', date)
        t3_techo   = param._get_parameter_value('rd_isr_t3_techo', date)
        t3_base    = param._get_parameter_value('rd_isr_t3_base', date)
        t4_base    = param._get_parameter_value('rd_isr_t4_base', date)

        # 1. Proyectar a mensual (quincena × 2) y anualizar (× 12)
        tss_emp_quincena = sfs_emp_quincena + afp_emp_quincena
        base_anual = (bruto_quincena - tss_emp_quincena) * 2 * 12

        # 2. Aplicar escala vigente
        if base_anual <= exencion:
            isr_anual = 0.0
        elif base_anual <= t2_techo:
            isr_anual = (base_anual - exencion) * 0.15
        elif base_anual <= t3_techo:
            isr_anual = t3_base + (base_anual - t2_techo) * 0.20
        else:
            isr_anual = t4_base + (base_anual - t3_techo) * 0.25

        isr_quincenal = isr_anual / 24.0

        # Guardar para auditoría
        self.rd_base_isr_anual = base_anual
        self.rd_isr_anual = isr_anual
        self.rd_isr_quincenal = isr_quincenal

        return isr_quincenal

    # ─── Función localcode accesible desde las reglas salariales ─────────────

    def _get_localdict(self, line, categories, rules, worked_days, inputs):
        """Inyecta _rd_isr al diccionario local para que las reglas puedan llamarlo."""
        localdict = super()._get_localdict(line, categories, rules, worked_days, inputs)
        localdict['_rd_isr'] = self._get_rd_isr
        return localdict
