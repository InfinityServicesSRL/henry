# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class AgsConfig(models.Model):
    """Configuracion contable del modulo.

    RAZON DE EXISTIR: el plan de cuentas de cada empresa es distinto. Calcular
    margen bruto asumiendo codigos de cuenta seria adivinar. Aqui se declara
    explicitamente que cuentas alimentan cada calculo.

    Por defecto se usan los TIPOS de cuenta de Odoo (income, expense_direct_cost),
    que la localizacion dominicana ya configura. Si esa clasificacion no refleja
    la realidad de AG Supply, se pueden especificar cuentas exactas y el sistema
    las usa en lugar del tipo.
    """
    _name = "ags.config"
    _description = "AG Intelligence - Configuracion Contable"
    _rec_name = "company_id"

    company_id = fields.Many2one(
        "res.company",
        string="Compania",
        required=True,
        default=lambda self: self.env.company,
    )

    # ------------------------------------------------------------------
    # Metodo de identificacion de cuentas
    # ------------------------------------------------------------------

    metodo_cuentas = fields.Selection(
        [
            ("tipo", "Por tipo de cuenta (automatico)"),
            ("explicito", "Por cuentas especificas (manual)"),
        ],
        string="Metodo de identificacion",
        default="tipo",
        required=True,
        help="El metodo por tipo usa la clasificacion estandar de Odoo y no "
             "requiere mantenimiento. El explicito da control total pero hay "
             "que actualizarlo cada vez que se crea una cuenta nueva.",
    )

    cuenta_ingreso_ids = fields.Many2many(
        "account.account",
        "ags_config_ingreso_rel",
        "config_id",
        "account_id",
        string="Cuentas de ingreso",
        domain="[('company_ids', 'in', company_id)]",
    )
    cuenta_excluir_ingreso_ids = fields.Many2many(
        "account.account",
        "ags_config_excl_ing_rel",
        "config_id",
        "account_id",
        string="Cuentas de ingreso a excluir",
        domain="[('company_ids', 'in', company_id)]",
        help="Diferencia cambiaria, intereses ganados, dividendos, venta de "
             "activos y otros ingresos no operativos. Deben excluirse porque "
             "el margen no debe moverse porque el dolar se movio.",
    )
    prefijo_ingreso_operativo = fields.Char(
        string="Prefijo de ingreso operativo",
        default="4101",
        help="Prefijo de las cuentas de venta de mercancia. Las cuentas de "
             "ingreso que no empiecen con este prefijo se excluyen del calculo "
             "de ventas netas.",
    )
    cuenta_costo_venta_ids = fields.Many2many(
        "account.account",
        "ags_config_costo_rel",
        "config_id",
        "account_id",
        string="Cuentas de costo de ventas",
        domain="[('company_ids', 'in', company_id)]",
    )
    cuenta_gasto_operativo_ids = fields.Many2many(
        "account.account",
        "ags_config_gasto_rel",
        "config_id",
        "account_id",
        string="Cuentas de gasto operativo",
        domain="[('company_ids', 'in', company_id)]",
        help="Gastos de administracion y ventas. NO incluir depreciacion, "
             "amortizacion ni intereses: el EBITDA los excluye por definicion.",
    )
    cuenta_excluir_ebitda_ids = fields.Many2many(
        "account.account",
        "ags_config_excl_rel",
        "config_id",
        "account_id",
        string="Cuentas a excluir del EBITDA",
        domain="[('company_ids', 'in', company_id)]",
        help="Depreciacion, amortizacion, intereses e impuesto sobre la renta. "
             "Si estas cuentas ya estan tipificadas correctamente en Odoo, "
             "este campo puede quedar vacio.",
    )

    cuenta_excluir_cxp_ids = fields.Many2many(
        "account.account",
        "ags_config_excl_cxp_rel",
        "config_id",
        "account_id",
        string="Cuentas a excluir de la CxP comercial",
        domain="[('company_ids', 'in', company_id)]",
        help="Provisiones laborales, depositos por identificar y otras "
             "cuentas por pagar que no son credito de proveedores. "
             "Ejemplo: Vacation Payable.",
    )
    cuenta_mod_ids = fields.Many2many(
        "account.account",
        "ags_config_mod_rel",
        "config_id",
        "account_id",
        string="Cuentas de mano de obra directa",
        domain="[('company_ids', 'in', company_id)]",
        help="Sueldos de planta, horas extras, incentivos, vacaciones y "
             "salario de navidad del personal de produccion. Se usa para medir "
             "el costo de conversion por separado del costo de materiales.",
    )

    # ------------------------------------------------------------------
    # Politica de pronto pago
    # ------------------------------------------------------------------

    pct_pronto_pago = fields.Float(
        string="Descuento por pronto pago (%)",
        digits=(5, 2),
        default=2.0,
        help="Porcentaje ofrecido por pago anticipado.",
    )
    dias_pronto_pago = fields.Integer(
        string="Plazo maximo para pronto pago (dias)",
        default=15,
        help="Dias dentro de los cuales el pago califica para el descuento. "
             "Si el cobro llega despues, el descuento se otorgo sin comprar "
             "el adelanto que justificaba su costo.",
    )
    dias_gracia_pronto_pago = fields.Integer(
        string="Dias de gracia",
        default=3,
        help="Tolerancia sobre el plazo antes de marcar el descuento como "
             "otorgado fuera de terminos.",
    )

    # ------------------------------------------------------------------
    # Materia prima y empaque
    # ------------------------------------------------------------------

    categoria_mp_ids = fields.Many2many(
        "product.category",
        "ags_config_cat_mp_rel",
        "config_id",
        "categ_id",
        string="Categorias de materia prima",
        help="Bobina madre y demas materia prima. Se usa para calcular el "
             "costo de MP sobre ventas y la merma de conversion.",
    )
    categoria_empaque_ids = fields.Many2many(
        "product.category",
        "ags_config_cat_emp_rel",
        "config_id",
        "categ_id",
        string="Categorias de material de empaque",
    )
    categoria_reproceso_ids = fields.Many2many(
        "product.category",
        "ags_config_cat_rep_rel",
        "config_id",
        "categ_id",
        string="Categorias de reproceso",
        help="Combos y productos que consumen articulos YA TERMINADOS de "
             "otras ordenes. Se excluyen del consumo de materia prima porque "
             "duplican el costo: la bobina se cuenta al hacer el jumbo y otra "
             "vez al armar el combo.",
    )
    categoria_pt_ids = fields.Many2many(
        "product.category",
        "ags_config_cat_pt_rel",
        "config_id",
        "categ_id",
        string="Categorias de producto terminado",
    )

    # ------------------------------------------------------------------
    # Cuentas de balance - alimentan los ratios financieros
    #
    # Los ratios de balance son foto a una fecha y acumulan desde el inicio
    # de operaciones, a diferencia de los del estado de resultados que son
    # de periodo. Mezclarlos exige cuidado con que fecha se toma cada cosa.
    # ------------------------------------------------------------------

    cuenta_inventario_ids = fields.Many2many(
        "account.account",
        "ags_config_inv_rel",
        "config_id",
        "account_id",
        string="Cuentas de inventario",
        domain="[('company_ids', 'in', company_id)]",
        help="Necesarias para la prueba acida, que excluye el inventario del "
             "activo circulante por ser el activo menos liquido.",
    )
    cuenta_efectivo_ids = fields.Many2many(
        "account.account",
        "ags_config_efe_rel",
        "config_id",
        "account_id",
        string="Cuentas de efectivo y equivalentes",
        domain="[('company_ids', 'in', company_id)]",
        help="Si se deja vacio se usan las cuentas de tipo Banco y Efectivo.",
    )
    cuenta_deuda_financiera_ids = fields.Many2many(
        "account.account",
        "ags_config_deuda_rel",
        "config_id",
        "account_id",
        string="Cuentas de deuda financiera",
        domain="[('company_ids', 'in', company_id)]",
        help="Prestamos bancarios, cooperativas y lineas de credito, corto y "
             "largo plazo. Si se deja vacio, la deuda financiera se deriva de "
             "los terceros marcados como acreedor financiero.",
    )
    cuenta_gasto_financiero_ids = fields.Many2many(
        "account.account",
        "ags_config_gfin_rel",
        "config_id",
        "account_id",
        string="Cuentas de gastos financieros",
        domain="[('company_ids', 'in', company_id)]",
        help="Intereses pagados. Alimentan la cobertura de intereses.",
    )
    cuenta_depreciacion_ids = fields.Many2many(
        "account.account",
        "ags_config_dep_rel",
        "config_id",
        "account_id",
        string="Cuentas de depreciacion y amortizacion",
        domain="[('company_ids', 'in', company_id)]",
        help="Si se deja vacio se usan las de tipo Depreciacion.",
    )

    # ------------------------------------------------------------------
    # Costo de capital - alimenta el margen economico
    # ------------------------------------------------------------------

    tasa_costo_capital = fields.Float(
        string="Tasa de costo de capital (% anual)",
        digits=(5, 2),
        default=12.3,
        required=True,
        help="Tasa anual usada para valorar el costo de financiar cuentas por "
             "cobrar. El valor por defecto (12.3%) es el promedio del sector "
             "industrial dominicano en moneda nacional segun la "
             "Superintendencia de Bancos. Reemplazar por la tasa real de "
             "AG Supply si se conoce.",
    )
    dias_base_anio = fields.Integer(
        string="Dias base del año",
        default=365,
        required=True,
    )

    # ------------------------------------------------------------------
    # Energia electrica
    # ------------------------------------------------------------------

    proveedor_energia_id = fields.Many2one(
        "res.partner",
        string="Proveedor de energia",
        help="EDE Norte. Se usa para identificar las facturas que llevan "
             "registro de consumo en kWh.",
    )
    energia_mes_anterior = fields.Boolean(
        string="Imputar energia al mes anterior",
        default=True,
        help="Las facturas de la distribuidora se reciben al mes siguiente del "
             "consumo. Con esta opcion activa, una factura fechada en febrero "
             "se imputa al consumo de enero. Sin esto, el ratio kWh por "
             "tonelada compara energia de un mes contra produccion de otro, "
             "y ese desfase es sistematico, no ruido que se promedie.",
    )

    # ------------------------------------------------------------------
    # Merma
    # ------------------------------------------------------------------

    metodo_merma = fields.Selection(
        [
            ("scrap", "Por registros de desecho (stock.scrap)"),
            ("variacion", "Por variacion contra consumo teorico de la LdM"),
        ],
        string="Metodo de calculo de merma",
        default="scrap",
        required=True,
        help="El metodo por desecho solo captura lo que se registra "
             "explicitamente como scrap. El metodo por variacion compara "
             "consumo real contra el teorico de la lista de materiales y "
             "captura toda la diferencia, incluida la no registrada.",
    )

    notas = fields.Text(string="Notas de configuracion")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("company_unica", "unique(company_id)",
         "Ya existe una configuracion para esta compania."),
    ]

    @api.constrains("tasa_costo_capital")
    def _check_tasa(self):
        for rec in self:
            if not (0 <= rec.tasa_costo_capital <= 100):
                raise ValidationError(
                    _("La tasa de costo de capital debe estar entre 0 y 100.")
                )

    @api.model
    def get_config(self, company=None):
        """Devuelve la configuracion de la compania, creandola si no existe."""
        company = company or self.env.company
        config = self.search([("company_id", "=", company.id)], limit=1)
        if not config:
            config = self.create({"company_id": company.id})
        return config

    # ------------------------------------------------------------------
    # Resolucion de cuentas
    # ------------------------------------------------------------------

    def _idioma_contable(self):
        """Idioma en que estan nombradas las cuentas de AG Supply.

        POR QUE IMPORTA: el plan de cuentas tiene nombres distintos en cada
        idioma, y las traducciones al ingles no son fieles. Ejemplos reales
        verificados en agosto 2026:

          31040100  es_DO "Resultados Acumulados"  vs  en_US "Legal Reserve"
          11050200  es_DO "Inventario de Materia Prima"
                    en_US "Allowance for doubtful accounts receivable"
          61010400  es_DO "Sueldos y Salarios"
                    en_US "Servicios profesionales, ARS y compensacion"

        Leer el nombre en ingles llevo a tres conclusiones equivocadas en el
        analisis: creer que una cuenta de resultados acumulados era una
        reserva legal, que el inventario de materia prima era una provision
        de cuentas por cobrar, y que la nomina incluia servicios
        profesionales.

        Cuando el modulo detecta cuentas por palabras en su nombre -- como la
        mano de obra directa -- tiene que hacerlo sobre el nombre en espanol.
        """
        self.ensure_one()
        return "es_DO"

    def _cuentas_por_tipo(self, tipos):
        """Devuelve las cuentas de la compania que coinciden con los tipos."""
        self.ensure_one()
        return self.env["account.account"].search([
            ("account_type", "in", tipos),
            ("company_ids", "in", self.company_id.id),
        ])

    def cuentas_ingreso(self):
        """Cuentas de venta operativa, ya depuradas.

        Se excluyen diferencia cambiaria, intereses, dividendos y venta de
        activos. Sin esta depuracion el margen bruto sube o baja segun se
        mueva el tipo de cambio, que no tiene nada que ver con la operacion.
        """
        self.ensure_one()
        if self.metodo_cuentas == "explicito" and self.cuenta_ingreso_ids:
            cuentas = self.cuenta_ingreso_ids
        else:
            cuentas = self._cuentas_por_tipo(["income"])
            if self.prefijo_ingreso_operativo:
                cuentas = cuentas.filtered(
                    lambda c: (c.code or "").startswith(self.prefijo_ingreso_operativo)
                )
        return cuentas - self.cuenta_excluir_ingreso_ids

    def cuentas_ingreso_no_operativo(self):
        """Las que quedaron fuera: sirven para reportar aparte, no para margen."""
        self.ensure_one()
        todas = self._cuentas_por_tipo(["income", "income_other"])
        return todas - self.cuentas_ingreso()

    def cuentas_mod(self):
        """Mano de obra directa dentro del costo de ventas.

        La deteccion es por palabras en el nombre de la cuenta, y se hace
        sobre el nombre en ESPANOL de forma explicita: si se leyera el nombre
        en el idioma del usuario, un usuario en ingles no encontraria ninguna
        coincidencia y la mano de obra saldria en cero sin aviso.
        """
        self.ensure_one()
        if self.cuenta_mod_ids:
            return self.cuenta_mod_ids
        claves = ["SUELDO", "SALARIO", "INCENTIVO", "HORAS EXTRA",
                  "VACACIONES", "ATENCIONES", "NAVIDAD", "BONIFICA",
                  "REGALIA", "PRESTACIONES", "TSS", "INFOTEP"]
        lang = self._idioma_contable()
        return self.cuentas_costo_venta().filtered(
            lambda c: any(
                k in (c.with_context(lang=lang).name or "").upper()
                for k in claves)
        )

    def cuentas_costo_venta(self):
        self.ensure_one()
        if self.metodo_cuentas == "explicito" and self.cuenta_costo_venta_ids:
            return self.cuenta_costo_venta_ids
        return self._cuentas_por_tipo(["expense_direct_cost"])

    def cuentas_gasto_operativo(self):
        self.ensure_one()
        if self.metodo_cuentas == "explicito" and self.cuenta_gasto_operativo_ids:
            return self.cuenta_gasto_operativo_ids
        cuentas = self._cuentas_por_tipo(["expense"])
        return cuentas - self.cuenta_excluir_ebitda_ids

    # ------------------------------------------------------------------
    # Resolucion de cuentas de balance
    # ------------------------------------------------------------------

    def cuentas_activo_circulante(self):
        self.ensure_one()
        return self._cuentas_por_tipo([
            "asset_receivable", "asset_cash", "asset_current", "asset_prepayments"])

    def cuentas_activo_total(self):
        self.ensure_one()
        return self._cuentas_por_tipo([
            "asset_receivable", "asset_cash", "asset_current", "asset_prepayments",
            "asset_non_current", "asset_fixed"])

    def cuentas_pasivo_corriente(self):
        self.ensure_one()
        return self._cuentas_por_tipo(["liability_payable", "liability_current"])

    def cuentas_pasivo_total(self):
        self.ensure_one()
        return self._cuentas_por_tipo([
            "liability_payable", "liability_current", "liability_non_current"])

    def cuentas_patrimonio(self):
        self.ensure_one()
        return self._cuentas_por_tipo(["equity", "equity_unaffected"])

    def cuentas_efectivo(self):
        self.ensure_one()
        if self.cuenta_efectivo_ids:
            return self.cuenta_efectivo_ids
        return self._cuentas_por_tipo(["asset_cash"])

    def cuentas_depreciacion(self):
        self.ensure_one()
        if self.cuenta_depreciacion_ids:
            return self.cuenta_depreciacion_ids
        return self._cuentas_por_tipo(["expense_depreciation"])

    # ------------------------------------------------------------------
    # Autoconfiguracion por codigo de cuenta
    # ------------------------------------------------------------------

    # Codigos del plan de cuentas de AG Supply. Se buscan por codigo y no se
    # referencian por ID porque los identificadores internos cambian entre
    # bases de datos: lo que es estable es el codigo contable.
    #
    # Si el plan de cuentas cambia, basta actualizar estas listas y volver a
    # ejecutar la autoconfiguracion desde el boton de la vista.
    CODIGOS_DEUDA_FINANCIERA = [
        # Codigos verificados por ID en agosto 2026. Son 2102xx y no 2103xx:
        # las cuentas 2103xx son Vacation Payable e ITBIS retenido.
        "21020100",  # CXP Sup. Financ. Personas Juridicas RD$
        "21020101",  # CXP Sup. Financ. Personas Juridicas US$
        "21020102",  # Prima CXP Sup. Financ. Personas Juridicas
        "21020200",  # CXP Sup. Financ. Personas Fisicas RD$
        "21020201",  # CXP Sup. Financ. Personas Fisicas US$
        "21020202",  # Prima CXP Sup. Financ. Personas Fisicas
        "21010205",  # Tarjeta de credito 0122
        "21010206",  # Tarjeta de credito 1103
        "21010207",  # Tarjeta de credito 5100
        "21010208",  # Tarjeta de credito 3773
    ]
    CODIGOS_GASTO_FINANCIERO = [
        "61010714",  # Otros intereses sobre prestamos
        "61010715",  # Gastos interes Banco Santa Cruz
        "61070300",  # Bank Interest
        "61010803",  # Exchange Loss
        "61070800",  # Foreign exchange losses
    ]

    def autoconfigurar_cuentas(self):
        """Asigna las cuentas conocidas del plan de AG Supply.

        Es idempotente: se puede ejecutar cuantas veces haga falta. Solo
        asigna las cuentas que encuentra, y deja constancia de las que no
        existen en lugar de fallar en silencio.

        Las cuentas de efectivo, inventario y depreciacion se detectan por
        tipo y no por codigo, porque esa clasificacion si es estandar.
        """
        Account = self.env["account.account"]
        resultado = {"asignadas": {}, "no_encontradas": []}

        def buscar(codigos):
            enc, falt = Account.browse(), []
            for c in codigos:
                a = Account.search([
                    ("code", "=", c),
                    ("company_ids", "in", self.company_id.id),
                ], limit=1)
                if a:
                    enc |= a
                else:
                    falt.append(c)
            return enc, falt

        for campo, codigos, etiqueta in [
            ("cuenta_deuda_financiera_ids", self.CODIGOS_DEUDA_FINANCIERA, "Deuda financiera"),
            ("cuenta_gasto_financiero_ids", self.CODIGOS_GASTO_FINANCIERO, "Gastos financieros"),
        ]:
            enc, falt = buscar(codigos)
            if enc:
                self[campo] = [(6, 0, enc.ids)]
                resultado["asignadas"][etiqueta] = len(enc)
            resultado["no_encontradas"].extend(falt)

        # Inventario: cuentas de valoracion declaradas en las categorias de
        # producto. Es mas confiable que adivinar por nombre o por codigo.
        categorias = (self.categoria_mp_ids | self.categoria_empaque_ids
                      | self.categoria_pt_ids)
        if not categorias:
            categorias = self.env["product.category"].search([])
        cuentas_inv = categorias.mapped(
            "property_stock_valuation_account_id").filtered(lambda a: a)
        if cuentas_inv:
            self.cuenta_inventario_ids = [(6, 0, cuentas_inv.ids)]
            resultado["asignadas"]["Inventario"] = len(cuentas_inv)

        return resultado

    @api.model
    def _autoconfigurar_al_instalar(self):
        """Punto de entrada desde los datos del modulo.

        Crea la configuracion si no existe y asigna las cuentas conocidas.
        Si el plan de cuentas es distinto simplemente no encuentra nada y
        deja los campos vacios, que es el comportamiento correcto: es
        preferible una configuracion vacia y visible a una mal adivinada.
        """
        cfg = self.get_config()
        cfg.autoconfigurar_cuentas()
        return True

    def action_autoconfigurar(self):
        """Boton de la vista: ejecuta la autoconfiguracion y muestra el resumen."""
        self.ensure_one()
        r = self.autoconfigurar_cuentas()
        lineas = ["%s: %s cuentas" % (k, v) for k, v in r["asignadas"].items()]
        if r["no_encontradas"]:
            lineas.append("")
            lineas.append("No encontradas en el plan: %s"
                          % ", ".join(r["no_encontradas"]))
        raise UserError(
            _("Autoconfiguracion completada\n\n%s") % "\n".join(lineas))

    def diagnosticar_traducciones(self, umbral=10):
        """Cuentas cuyo nombre en ingles difiere del espanol.

        Existe porque tres conclusiones del analisis de agosto 2026 fueron
        equivocadas por leer el nombre en ingles de una cuenta. Las
        traducciones del plan de cuentas de AG Supply no son fieles: hay
        casos donde el nombre en ingles describe una cuenta completamente
        distinta.

        Devuelve las divergencias para que quien interprete un saldo sepa
        que el nombre que esta leyendo puede no corresponder.
        """
        self.ensure_one()
        salida = []
        for c in self.env["account.account"].search([
                ("company_ids", "in", self.company_id.id)]):
            es = (c.with_context(lang="es_DO").name or "").strip()
            en = (c.with_context(lang="en_US").name or "").strip()
            if not es or not en or es == en:
                continue
            # Divergencia real: no comparten ni la primera palabra significativa
            pe = {w for w in es.upper().split() if len(w) > 3}
            pn = {w for w in en.upper().split() if len(w) > 3}
            if pe & pn:
                continue
            salida.append({
                "code": c.code, "es": es, "en": en,
                "account_type": c.account_type,
            })
        return salida

    def action_diagnosticar_traducciones(self):
        """Boton de la vista: muestra las cuentas con nombres divergentes."""
        self.ensure_one()
        d = self.diagnosticar_traducciones()
        if not d:
            raise UserError(_("Todas las cuentas tienen nombres coherentes "
                              "entre espanol e ingles."))
        lineas = ["%s\n   es: %s\n   en: %s" % (x["code"], x["es"], x["en"])
                  for x in d[:25]]
        extra = ""
        if len(d) > 25:
            extra = _("\n\nY %s cuentas mas.") % (len(d) - 25)
        raise UserError(
            _("%(n)s cuentas tienen nombres que no coinciden entre idiomas.\n\n"
              "El modulo lee siempre el nombre en espanol. Esta lista sirve "
              "para saber cuando el nombre en ingles puede confundir a quien "
              "interprete un reporte.\n\n%(l)s%(e)s") % {
                "n": len(d), "l": "\n\n".join(lineas), "e": extra})

    def action_validar(self):
        """Comprueba que la configuracion permita calcular, y avisa si no."""
        self.ensure_one()
        problemas = []
        if not self.cuentas_ingreso():
            problemas.append(_("No se identificaron cuentas de ingreso."))
        if not self.cuentas_costo_venta():
            problemas.append(_("No se identificaron cuentas de costo de ventas."))
        if not self.categoria_mp_ids:
            problemas.append(
                _("No hay categorias de materia prima declaradas: el costo de "
                  "MP sobre ventas y la merma no se podran calcular.")
            )
        if not self.proveedor_energia_id:
            problemas.append(
                _("No hay proveedor de energia declarado: el consumo por "
                  "tonelada no se podra calcular.")
            )
        if problemas:
            raise UserError(
                _("Configuracion incompleta:\n\n- %s") % "\n- ".join(problemas)
            )
        raise UserError(
            _("Configuracion valida.\n\n"
              "Cuentas de ingreso: %(ing)s\n"
              "Cuentas de costo de ventas: %(cv)s\n"
              "Cuentas de gasto operativo: %(go)s\n"
              "Categorias de materia prima: %(mp)s")
            % {
                "ing": len(self.cuentas_ingreso()),
                "cv": len(self.cuentas_costo_venta()),
                "go": len(self.cuentas_gasto_operativo()),
                "mp": len(self.categoria_mp_ids),
            }
        )
