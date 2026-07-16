{
    'name': 'AG Supply — Nómina República Dominicana',
    'version': '18.0.1.5.0',
    'summary': 'Módulo de nómina quincenal con legislación RD 2026 e incentivo de producción',
    'author': 'AG Supply, SRL.',
    'website': '',
    'category': 'Human Resources/Payroll',
    'license': 'LGPL-3',
    'depends': [
        'hr_payroll',       # Enterprise — motor de nómina
        'hr_attendance',    # Asistencias — fuente oficial de horas
        'mrp',              # Manufactura — fuente de incentivo de producción
        'account',          # Contabilidad — asientos de nómina
    ],
    'data': [
        'security/ir.model.access.csv',
'data/hr_rule_parameter_data.xml',       # Parámetros TSS/ISR 2026 (con date_from)
        'data/hr_work_entry_type_data.xml',      # Tipos de work entry dominicanos
        'data/hr_payslip_input_type_data.xml',   # INCPROD, COMISION, HE35, HE100, etc.
        'data/hr_payroll_structure_type_data.xml',  # Tipos de estructura (Producción, Admin, Ventas)
        'data/hr_payroll_structure_data.xml',    # Estructuras quincenal + regalía + liquidación
        'data/hr_salary_rule_data.xml',          # Reglas TSS, ISR, incentivos
        'views/ag_incentivo_views.xml',
        'views/mrp_operation_views.xml',   # Campo incentivo RD$/unidad en la operación
        'views/mrp_workorder_views.xml',   # Trazabilidad incentivo pendiente/pagado
        'views/hr_payslip_views.xml',
        'report/report_payslip_rd.xml',
    ],
    'demo': [],
    'installable': True,
    'auto_install': False,
    'application': False,
}
