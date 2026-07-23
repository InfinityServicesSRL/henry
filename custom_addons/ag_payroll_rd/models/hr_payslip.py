from odoo import models, fields


class HrPayslip(models.Model):
    """
    Extensión del recibo de nómina para nómina dominicana.

    Agrega campos de trazabilidad ISR (útiles para auditoría con DGII/Angélica)
    y el método _get_rd_isr() que centraliza el cálculo ISR anualizado.
    La regla salarial ISR llama directamente a este método para evitar duplicación.
    """
    _inherit = 'hr.payslip'

    # ─── Campos de trazabilidad ISR ───────────────────────────────────────────
    rd_base_isr_anual = fields.Float(
        string='Base ISR anualizada (RD$)',
        digits=(12, 2),
        readonly=True,
        help='Salario bruto anual proyectado menos TSS empleado. Base de la escala DGII.',
    )
    rd_isr_anual = fields.Float(
        string='ISR anual calculado (RD$)',
        digits=(12, 2),
        readonly=True,
        help='ISR resultante de aplicar la escala DGII vigente a la base anualizada.',
    )
    rd_isr_quincenal = fields.Float(
        string='Retención ISR quincenal (RD$)',
        digits=(12, 2),
        readonly=True,
        help='ISR anual / 24 períodos. Coincide con el monto en la línea ISR del recibo.',
    )

    # ─── Motor ISR — llamado desde la regla salarial ──────────────────────────

    def _get_rd_isr(self, bruto_quincena, sfs_emp_quincena, afp_emp_quincena):
        """
        Calcula y registra la retención ISR quincenal (escala DGII vigente).

        Parámetros
        ----------
        bruto_quincena : float
            GROSS del período (suma de categoría ALW: sueldo + extras + INCPROD + comisión - ausencias).
        sfs_emp_quincena : float
            Total de la línea SFS_EMP (valor negativo en Odoo — se aplica abs()).
        afp_emp_quincena : float
            Total de la línea AFP_EMP (valor negativo en Odoo — se aplica abs()).

        Retorna
        -------
        float
            Retención ISR quincenal a descontar al empleado.

        Notas
        -----
        - Los parámetros rd_isr_* se leen desde hr.rule.parameter con date_from,
          por lo que la escala 2027 (Ley 30-26) entrará automáticamente el 01-ene-2027
          sin necesidad de modificar este código.
        - La regalía pascual y la bonificación Art. 223 tienen sus propias
          estructuras de nómina con reglas ISR independientes.
        """
        param = self.env['hr.rule.parameter']
        date = self.date_from or fields.Date.today()

        exencion = param._get_parameter_from_code('rd_isr_exencion', date)
        t2_techo  = param._get_parameter_from_code('rd_isr_t2_techo', date)
        t3_techo  = param._get_parameter_from_code('rd_isr_t3_techo', date)
        t3_base   = param._get_parameter_from_code('rd_isr_t3_base', date)
        t4_base   = param._get_parameter_from_code('rd_isr_t4_base', date)

        # TSS empleado quincena (SFS + AFP, en valor absoluto)
        tss_emp = abs(sfs_emp_quincena) + abs(afp_emp_quincena)

        # Base anual: proyectar quincena a mensual (×2) y a anual (×12)
        base_anual = (bruto_quincena - tss_emp) * 2 * 12

        # Escala progresiva DGII
        if base_anual <= exencion:
            isr_anual = 0.0
        elif base_anual <= t2_techo:
            isr_anual = (base_anual - exencion) * 0.15
        elif base_anual <= t3_techo:
            isr_anual = t3_base + (base_anual - t2_techo) * 0.20
        else:
            isr_anual = t4_base + (base_anual - t3_techo) * 0.25

        isr_quincenal = isr_anual / 24.0

        # Guardar campos de trazabilidad (útil para revisión con Angélica / DGII)
        # Usamos _write para evitar disparar recomputaciones innecesarias
        self._write({
            'rd_base_isr_anual': base_anual,
            'rd_isr_anual': isr_anual,
            'rd_isr_quincenal': isr_quincenal,
        })

        return isr_quincenal
