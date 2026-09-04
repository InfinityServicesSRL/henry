# -*- coding: utf-8 -*-
import logging

from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)

# Campos de ags.config sin los cuales hay calculadores que devuelven cero por
# construccion. No es una lista de "estaria bien tenerlos": cada uno tiene al
# menos un indicador que miente en silencio si esta vacio -- fue exactamente
# lo que paso con las categorias de materia prima hasta el 27 de agosto.
CONFIG_REQUERIDA = [
    ("categoria_mp_ids", "Categorias de materia prima"),
    ("categoria_empaque_ids", "Categorias de material de empaque"),
    ("categoria_pt_ids", "Categorias de producto terminado"),
    ("cuenta_ingreso_ids", "Cuentas de ingreso"),
    ("cuenta_costo_venta_ids", "Cuentas de costo de ventas"),
    ("cuenta_inventario_ids", "Cuentas de inventario"),
]


class AgsAuditor(models.AbstractModel):
    """Motor de evaluacion de reglas.

    Copia deliberada del patron de cron_calcular_mediciones, con sus lecciones
    ya pagadas:

      - CADA REGLA EN SU PROPIO SAVEPOINT. Atrapar la excepcion de Python no
        basta: si el fallo vino de una consulta invalida, Postgres deja la
        transaccion abortada y toda consulta posterior responde "current
        transaction is aborted". Una regla rota tumbaria la auditoria entera
        y llenaria el log de errores en cascada que esconden cual fue el
        primero.
      - UNA REGLA SIN METODO SE SALTA EN SILENCIO, no rompe la corrida.

    Y tres propias:

      - CENSAL, NO MUESTRAL. Una regla barre el universo completo de su
        modelo. Si hay 278 fichas mal, dice 278 y entrega el dominio para
        abrirlas. Una auditoria por muestreo no sirve para corregir.
      - POR COMPANIA cuando la regla lee campos company_dependent. Sin eso
        mide la compania activa de quien corrio la evaluacion y parece un dato
        de la empresa (D12).
      - IDEMPOTENTE. Correrla dos veces no duplica hallazgos ni mueve
        primera_deteccion.
    """
    _name = "ags.auditor"
    _description = "AG Intelligence - Motor de auditoria"

    # ==================================================================
    # Orquestacion
    # ==================================================================

    @api.model
    def evaluar_reglas(self, codigos=None, frecuencia=None):
        """Evalua las reglas activas y reconcilia sus hallazgos.

        Devuelve el resumen por regla, que es lo que se mira en el shell
        mientras la Etapa 8.6 no tenga pantalla.
        """
        dominio = [("activa", "=", True)]
        if codigos:
            dominio.append(("codigo", "in", codigos))
        if frecuencia:
            dominio.append(("frecuencia", "=", frecuencia))
        reglas = self.env["ags.regla"].search(dominio)

        # DOS PASADAS, y el orden entre ellas es parte del contrato.
        #
        # La supresion por causa raiz consulta los hallazgos vivos en el
        # momento de evaluar cada regla. Si las reglas se recorren en el orden
        # del modelo, una regla de detalle que alfabeticamente vaya antes que
        # su causa raiz se evalua cuando la raiz todavia no existe, y audita
        # un terreno que un segundo despues queda suprimido. El resultado
        # entonces depende del orden de los codigos y de cuantas veces se
        # corrio, y una auditoria asi no vale: la cifra tiene que ser la misma
        # siempre.
        raiz = reglas.filtered("suprime_detalle")
        resto = reglas - raiz

        resumen = {}
        fallidas = []
        for regla in list(raiz) + list(resto):
            if not hasattr(self, regla.metodo_tecnico or ""):
                continue
            try:
                with self.env.cr.savepoint():
                    resumen[regla.codigo] = self._evaluar_regla(regla)
                    regla.ultima_evaluacion = fields.Datetime.now()
                    # El savepoint aisla los fallos, pero la pasada de causa
                    # raiz tiene que quedar visible para las consultas de la
                    # segunda: se vacia el cache de escrituras pendientes.
                    self.env.flush_all()
            except Exception:
                fallidas.append(regla.codigo)
                _logger.exception(
                    "ags.auditor: fallo la regla %s (%s)",
                    regla.codigo, regla.metodo_tecnico)
                continue

        if fallidas:
            _logger.warning(
                "ags.auditor: %s de %s reglas fallaron: %s",
                len(fallidas), len(reglas), ", ".join(fallidas))
        return resumen

    @api.model
    def _evaluar_regla(self, regla):
        """Resuelve sobre que companias corre la regla y reconcilia cada una.

        Dos modos, y la diferencia no es cosmetica:

          - por_compania: la regla LEE campos company_dependent, asi que se
            ejecuta una vez por compania y siempre con with_company(). Sin eso
            devolveria el valor de la compania activa de quien corrio la
            evaluacion y pareceria un dato de la empresa (D12).
          - si no: la regla se ejecuta una sola vez, tipicamente porque
            COMPARA companias entre si. En ese caso cada hallazgo puede
            declarar a que compania pertenece.

        En los dos modos hay que visitar tambien las companias que hoy no
        devolvieron nada pero tienen hallazgos vivos de esta regla: si no, lo
        que ya se corrigio nunca se cerraria.
        """
        Compania = self.env["res.company"]
        lotes = {}

        suprimidas = () if regla.suprime_detalle else self._companias_suprimidas()

        if regla.por_compania:
            for compania in Compania.search([]):
                if compania.id in suprimidas:
                    # No se evalua, y al no aparecer en los lotes sus
                    # hallazgos abiertos se cierran solos mas abajo. Es el
                    # comportamiento correcto: dejaron de ser el problema.
                    continue
                # with_company() mueve la compania al frente de
                # allowed_company_ids pero deja las demas dentro, y las reglas
                # de registro multiempresa filtran por ESA lista: sin acotarla
                # una regla ve los datos de todas y devuelve el mismo numero
                # para cada una. Paso en la primera corrida real, con las 96
                # lineas de inventario negativo repetidas en las dos.
                auditor = self.with_company(compania).with_context(
                    allowed_company_ids=[compania.id])
                lotes[compania.id] = getattr(
                    auditor, regla.metodo_tecnico)(regla, compania) or []
        else:
            propia = self.env.company
            lotes[propia.id] = []
            for dato in getattr(self, regla.metodo_tecnico)(regla, propia) or []:
                lotes.setdefault(dato.get("compania_id") or propia.id,
                                 []).append(dato)

        abiertas = self.env["ags.hallazgo"].search([
            ("regla_id", "=", regla.id),
            ("estado", "in", ["abierto", "en_curso", "aceptado"]),
        ]).mapped("compania_id").ids
        for cid in abiertas:
            lotes.setdefault(cid, [])

        total = {"nuevos": 0, "reincidentes": 0, "corregidos": 0, "vivos": 0}
        for cid, encontrados in lotes.items():
            parcial = self._conciliar(regla, Compania.browse(cid), encontrados)
            for clave in total:
                total[clave] += parcial[clave]
        return total

    @api.model
    def _conciliar(self, regla, compania, encontrados):
        """Cruza lo detectado hoy contra lo que ya estaba abierto.

        Las tres transiciones que importan:
          - lo que aparece por primera vez        -> hallazgo nuevo
          - lo que se habia cerrado y volvio      -> se REABRE el mismo
            registro y sube reincidencias, sin tocar primera_deteccion (D13)
          - lo que ya no aparece                  -> cerrado_auto, con fecha
            y motivo. Nunca se borra
        """
        Hallazgo = self.env["ags.hallazgo"]
        hoy = fields.Date.context_today(self)

        vivos = Hallazgo.search([
            ("regla_id", "=", regla.id),
            ("compania_id", "=", compania.id),
            ("estado", "in", ["abierto", "en_curso", "aceptado"]),
        ])
        por_clave = {h.clave: h for h in vivos}

        res = {"nuevos": 0, "reincidentes": 0, "corregidos": 0, "vivos": 0}
        vigentes = set()

        for dato in encontrados:
            if dato.get("cantidad", 0) <= regla.tolerancia:
                continue
            clave = dato["clave"]
            vigentes.add(clave)
            vals = {
                "sujeto": dato["sujeto"],
                "cantidad": dato.get("cantidad", 0),
                "modelo": dato.get("modelo") or False,
                "dominio": str(dato["dominio"]) if dato.get("dominio") else False,
                "ultima_deteccion": hoy,
            }
            existente = por_clave.get(clave)
            if existente:
                existente.write(vals)
                res["vivos"] += 1
                continue

            # Puede existir cerrado de una corrida anterior. Se reabre el
            # mismo registro: crear otro perderia primera_deteccion, que es
            # la unica respuesta a "desde cuando lo saben".
            previo = Hallazgo.search([
                ("clave", "=", clave),
                ("compania_id", "=", compania.id),
            ], limit=1)
            if previo:
                previo.write(dict(
                    vals,
                    estado="abierto",
                    reincidencias=previo.reincidencias + 1,
                    fecha_resolucion=False,
                    resuelto_por_id=False,
                    ultima_reincidencia=hoy,
                ))
                previo.message_post(body=_(
                    "Vuelve a detectarse tras haberse cerrado. Reincidencia "
                    "numero %s: la correccion anterior no pego.")
                    % previo.reincidencias)
                res["reincidentes"] += 1
                res["vivos"] += 1
                continue

            nuevo = Hallazgo.create(dict(
                vals,
                regla_id=regla.id,
                compania_id=compania.id,
                clave=clave,
                primera_deteccion=hoy,
            ))
            # El nacimiento se escribe en el expediente del propio hallazgo.
            # El correo del dia habla de varios a la vez y no sirve como
            # historia de este.
            nuevo.message_post(body=_(
                "Detectado por primera vez el %(fecha)s. Regla: %(regla)s. "
                "Registros que incumplen: %(n)s.") % {
                    "fecha": hoy, "regla": regla.codigo,
                    "n": vals.get("cantidad", 0)})
            res["nuevos"] += 1
            res["vivos"] += 1

        corregidos = vivos.filtered(lambda h: h.clave not in vigentes)
        if corregidos:
            corregidos.write({
                "estado": "cerrado_auto",
                "fecha_resolucion": hoy,
                "nota_cierre": _("La regla dejo de detectarlo el %s.") % hoy,
            })
            res["corregidos"] = len(corregidos)
        return res

    # cron_auditoria_diaria vive en ags_auditor_aviso.py desde 8.8.0:
    # ahi evalua y ademas avisa lo que cambio.

    # ==================================================================
    # Utilidades comunes
    # ==================================================================

    @api.model
    def _companias_suprimidas(self):
        """Companias cuyo detalle no se audita porque hay una causa raiz.

        Devuelve los ids de las companias con un hallazgo vivo de alguna
        regla marcada suprime_detalle. Es un conjunto, no una lista de
        codigos escritos en el codigo: manana otra regla puede necesitar lo
        mismo y basta con marcarle la casilla.
        """
        return set(self.env["ags.hallazgo"].search([
            ("vivo", "=", True),
            ("regla_id.suprime_detalle", "=", True),
        ]).mapped("compania_id").ids)

    @api.model
    def _cuentas_en_idioma(self, cuentas):
        """Las cuentas con su nombre leido en es_DO.

        D9: en en_US el sufijo "NO USAR" de la cuenta 11050100 desaparece, y
        la regla que lo busca no detectaria absolutamente nada.
        """
        cfg = self.env["ags.config"].get_config()
        return cuentas.with_context(lang=cfg._idioma_contable())

    @api.model
    def _categorias_con_almacenables(self):
        """Categorias que tienen productos almacenables asignados DIRECTAMENTE.

        Directamente y no por jerarquia: product_count de Odoo es recursivo e
        incluye las hijas, de modo que un padre vacio aparenta tener 96
        productos. Confundirlo llevo a creer que los tres padres del plan de
        AG tenian existencias cuando no tienen ninguna.
        """
        Producto = self.env["product.template"]
        grupos = Producto._read_group(
            [("is_storable", "=", True)], ["categ_id"], ["__count"])
        return {categ.id: cantidad for categ, cantidad in grupos}

    # ==================================================================
    # Familia: configuracion
    # ==================================================================

    @api.model
    def _regla_categ_periodica_con_producto(self, regla, compania):
        """Categorias con producto almacenable que no generan asiento.

        Es la causa probable de que el consumo de materia prima de agosto
        (3,583,538.80) sea MAYOR que el costo de ventas contable del mes
        (2,976,070.96). La parte no puede ser mayor que el todo: hay consumo
        de inventario que no llega a contabilidad.

        Un hallazgo por categoria y no uno agregado: cada categoria es una
        decision distinta para el auditor, con su propia fecha de corte.
        """
        conteo = self._categorias_con_almacenables()
        salida = []
        for categ in self.env["product.category"].search(
                [("property_valuation", "=", "manual_periodic")]):
            n = conteo.get(categ.id, 0)
            if not n:
                continue
            salida.append({
                "clave": "%s:%s" % (regla.codigo, categ.id),
                "sujeto": categ.complete_name,
                "cantidad": n,
                "modelo": "product.template",
                "dominio": [("categ_id", "=", categ.id),
                            ("is_storable", "=", True)],
            })
        return salida

    @api.model
    def _regla_categ_cuenta_prohibida(self, regla, compania):
        """Categorias que valoran contra una cuenta marcada NO USAR.

        El plan contable de AG escribe la instruccion operativa dentro del
        propio nombre de la cuenta. Es una convencion util y fragil: en ingles
        el sufijo se pierde, asi que el nombre se lee siempre en es_DO (D9).
        """
        cfg = self.env["ags.config"].get_config(compania)
        marcador = (cfg.marcador_cuenta_prohibida or "").strip().upper()
        if not marcador:
            return []

        # Solo las que POSTEAN. Una categoria en inventario periodico apunta
        # a la cuenta prohibida pero no escribe nada en ella: senalarla como
        # grave llena la lista de hallazgos que no hacen dano. En la primera
        # corrida eran 13 de 15, y una lista asi ensena a ignorarla.
        #
        # No se pierde nada: si alguien pasa esa categoria a tiempo real
        # -- que es justamente la correccion que pide
        # CATEG_PERIODICA_CON_PRODUCTO -- esta regla la ve al dia siguiente.
        salida = []
        for categ in self.env["product.category"].search(
                [("property_valuation", "=", "real_time")]):
            cuenta = categ.property_stock_valuation_account_id
            if not cuenta:
                continue
            nombre = self._cuentas_en_idioma(cuenta).name or ""
            if marcador not in nombre.upper():
                continue
            salida.append({
                "clave": "%s:%s" % (regla.codigo, categ.id),
                "sujeto": "%s postea a %s" % (categ.complete_name, nombre),
                "cantidad": 1,
                "modelo": "product.category",
                "dominio": [("id", "=", categ.id)],
            })
        return salida

    @api.model
    def _regla_categ_cuenta_divergente(self, regla, compania):
        """Categorias hermanas que valoran contra cuentas distintas.

        Una familia partida en dos cuentas hace que cualquier lectura por
        cuenta sea incompleta sin que nadie lo note. Se senala al padre, que
        es donde se ve la incoherencia.
        """
        salida = []
        Categoria = self.env["product.category"]
        for padre in Categoria.search([("child_id", "!=", False)]):
            hijas = padre.child_id
            cuentas = {h.property_stock_valuation_account_id.id
                       for h in hijas if h.property_stock_valuation_account_id}
            if len(cuentas) < 2:
                continue
            salida.append({
                "clave": "%s:%s" % (regla.codigo, padre.id),
                "sujeto": "%s: %s cuentas distintas entre sus %s hijas" % (
                    padre.complete_name, len(cuentas), len(hijas)),
                "cantidad": len(cuentas),
                "modelo": "product.category",
                "dominio": [("id", "in", hijas.ids)],
            })
        return salida

    @api.model
    def _regla_compania_config_divergente(self, regla, compania):
        """Companias con la valoracion configurada de forma distinta.

        Nace de un hallazgo concreto: la segunda compania tiene las 33
        categorias en periodico apuntando a una cuenta de anticipos a
        suplidores. Nadie la configuro nunca, y todo el diagnostico de agosto
        se hizo sin saberlo sobre una sola compania.

        La regla se evalua una vez (no por compania) porque COMPARA
        companias. El hallazgo se le cuelga a la divergente.
        """
        principal = self.env.ref("base.main_company", raise_if_not_found=False)
        if not principal:
            return []
        Categoria = self.env["product.category"]
        categorias = Categoria.search([])
        if not categorias:
            return []

        base = {
            c.id: (c.property_valuation,
                   c.property_stock_valuation_account_id.id)
            for c in categorias.with_company(principal)
        }

        salida = []
        suprimidas = self._companias_suprimidas()
        for otra in self.env["res.company"].search([("id", "!=", principal.id)]):
            # Si la compania comparte RNC, su configuracion divergente no es
            # el hallazgo: el hallazgo es que exista. Senalarlo aqui tambien
            # seria contar dos veces el mismo hecho.
            if otra.id in suprimidas:
                continue
            distintas = [
                c for c in categorias.with_company(otra)
                if base.get(c.id) != (c.property_valuation,
                                      c.property_stock_valuation_account_id.id)
            ]
            if not distintas:
                continue
            salida.append({
                "clave": "%s:%s" % (regla.codigo, otra.id),
                "sujeto": "%s: %s de %s categorias configuradas distinto que %s"
                          % (otra.name, len(distintas), len(categorias),
                             principal.name),
                "cantidad": len(distintas),
                "modelo": "product.category",
                "dominio": [("id", "in", [c.id for c in distintas])],
                "compania_id": otra.id,
            })
        return salida

    @api.model
    def _regla_compania_duplica_rnc(self, regla, compania):
        """Companias que comparten RNC: no son entidades distintas.

        Una compania de Odoo es una entidad legal. Usarla para separar una
        localidad parte el libro mayor: los traslados entre plantas dejan de
        ser movimientos internos y pasan a ser operaciones intercompania, el
        inventario no consolida, y todo indicador calculado sobre la compania
        activa excluye la otra en silencio.

        AG Supply lo tiene asi por una decision de la primera fase de la
        implementacion que despues se revirtio: SDQ quedo mejor como
        localidad. Corregirlo es trabajo del proyecto de implementacion, no de
        este modulo. Lo que le toca al modulo es que la cifra no se lea como
        si fuera de toda la empresa sin decir que hay una parte afuera.

        Se senala a la compania mas nueva del grupo, no a la principal: es la
        que sobra.
        """
        grupos = {}
        for c in self.env["res.company"].search([]):
            clave = (c.vat or "").replace("-", "").replace(" ", "").upper()
            if not clave:
                continue
            grupos.setdefault(clave, []).append(c)

        salida = []
        for clave, companias in grupos.items():
            if len(companias) < 2:
                continue
            companias.sort(key=lambda c: c.id)
            principal = companias[0]
            for sobrante in companias[1:]:
                salida.append({
                    "clave": "%s:%s" % (regla.codigo, sobrante.id),
                    "sujeto": "%s comparte el RNC %s con %s: es una "
                              "localidad, no una entidad" % (
                                  sobrante.name, clave, principal.name),
                    "cantidad": 1,
                    "modelo": "res.company",
                    "dominio": [("id", "=", sobrante.id)],
                    "compania_id": sobrante.id,
                })
        return salida

    @api.model
    def _regla_control_recepcion(self, regla, compania):
        """Productos comprables que facturan contra cantidad PEDIDA.

        Con esa politica se puede facturar lo que aun no ha llegado y la
        conciliacion a tres bandas ni se activa. Es la palanca que deja crecer
        el saldo de bienes recibidos no facturados, hoy con 272 dias de
        antiguedad promedio ponderada.

        Un solo hallazgo agregado: son 278 fichas y se corrigen en masa, no
        una por una.
        """
        dominio = [("purchase_ok", "=", True), ("is_storable", "=", True),
                   ("purchase_method", "!=", "receive")]
        n = self.env["product.template"].search_count(dominio)
        if not n:
            return []
        total = self.env["product.template"].search_count(
            [("purchase_ok", "=", True), ("is_storable", "=", True)])
        return [{
            "clave": regla.codigo,
            "sujeto": "%s de %s fichas comprables facturan contra cantidad "
                      "pedida" % (n, total),
            "cantidad": n,
            "modelo": "product.template",
            "dominio": dominio,
        }]

    @api.model
    def _regla_config_modulo_incompleta(self, regla, compania):
        """Campos de ags.config que, vacios, hacen mentir a un calculador.

        No es una lista de deseos. Con categoria_mp_ids vacio, _consumo_mp
        devuelve cero POR CONSTRUCCION y MP_PCT_VENTAS reporta 0.00% sin que
        nada avise. Eso estuvo pasando hasta el 27 de agosto.
        """
        cfg = self.env["ags.config"].get_config(compania)
        vacios = [etiqueta for campo, etiqueta in CONFIG_REQUERIDA
                  if not cfg[campo]]
        if not vacios:
            return []
        return [{
            "clave": "%s:%s" % (regla.codigo, cfg.id),
            "sujeto": "Sin declarar: %s" % ", ".join(vacios),
            "cantidad": len(vacios),
            "modelo": "ags.config",
            "dominio": [("id", "=", cfg.id)],
        }]

    # ==================================================================
    # Familia: habito de registro
    # ==================================================================

    @api.model
    def _regla_sin_registro_merma(self, regla, compania):
        """Ordenes de produccion cerradas sin un solo registro de desecho.

        Para un fabricante de tissue, merma cero no es un logro: es merma no
        registrada. El desperdicio termina diluido en el costo del producto en
        vez de aparecer como una partida analizable. Las 139 listas de
        materiales tienen control de consumo configurado, asi que la
        herramienta esta puesta -- falta el habito.
        """
        desde = fields.Date.context_today(self) - relativedelta(days=30)
        Produccion = self.env["mrp.production"]
        cerradas = Produccion.search([
            ("state", "=", "done"),
            ("date_finished", ">=", "%s 00:00:00" % desde),
            ("company_id", "=", compania.id),
        ])
        if not cerradas:
            return []
        con_merma = set(self.env["stock.scrap"].search([
            ("production_id", "in", cerradas.ids),
            ("state", "=", "done"),
        ]).mapped("production_id").ids)
        sin_merma = cerradas.filtered(lambda p: p.id not in con_merma)
        if not sin_merma:
            return []
        return [{
            "clave": regla.codigo,
            "sujeto": "%s de %s ordenes cerradas en 30 dias sin registro de "
                      "desecho" % (len(sin_merma), len(cerradas)),
            "cantidad": len(sin_merma),
            "modelo": "mrp.production",
            "dominio": [("id", "in", sin_merma.ids)],
        }]

    @api.model
    def _regla_costo_estandar_en_cero(self, regla, compania):
        """Productos con costeo estandar y costo estandar en cero.

        Se eligio este corte y no "el costo lleva N meses sin actualizarse"
        porque Odoo no guarda de forma fiable la fecha del ultimo cambio de
        costo, y una regla que se apoya en un dato dudoso produce hallazgos
        que nadie cree. Un costo estandar en cero, en cambio, es
        inequivocamente incorrecto: valora a cero todo lo que se produzca.
        """
        productos = self.env["product.product"].search([
            ("is_storable", "=", True),
            ("categ_id.property_cost_method", "=", "standard"),
        ])
        malos = productos.filtered(lambda p: not p.standard_price)
        if not malos:
            return []
        return [{
            "clave": regla.codigo,
            "sujeto": "%s productos con costeo estandar y costo en cero"
                      % len(malos),
            "cantidad": len(malos),
            "modelo": "product.product",
            "dominio": [("id", "in", malos.ids)],
        }]

    @api.model
    def _regla_producto_duplicado(self, regla, compania):
        """Fichas activas que comparten nombre exacto.

        El patron que lo motivo: tres fichas creadas en agosto con el nombre
        identico a una que ya existia y arrastraba cantidad negativa.
        Duplicar la ficha en vez de corregir el negativo reparte el historial
        del mismo producto entre varios registros y deja el costo sin serie.
        """
        # Se agrupa en Python y no con _read_group: product.template.name es
        # un campo traducible (jsonb en Odoo 18) y agrupar por el no da un
        # resultado fiable. Son unos miles de fichas, cabe de sobra.
        from collections import Counter
        cuenta = Counter(
            (p["name"] or "").strip()
            for p in self.env["product.template"].search_read(
                [("active", "=", True)], ["name"])
        )
        nombres = [n for n, veces in cuenta.items() if n and veces > 1]
        if not nombres:
            return []
        return [{
            "clave": regla.codigo,
            "sujeto": "%s nombres con mas de una ficha activa" % len(nombres),
            "cantidad": len(nombres),
            "modelo": "product.template",
            "dominio": [("name", "in", nombres), ("active", "=", True)],
        }]

    # ==================================================================
    # Familia: integridad del dato
    # ==================================================================

    @api.model
    def _regla_inventario_negativo(self, regla, compania):
        """Ubicaciones internas con cantidad negativa.

        No es un indicador con banda tolerable: es un dato imposible.
        Cualquier cantidad distinta de cero invalida costo y margen, porque el
        sistema esta valorando salidas de existencias que nunca entraron.
        """
        dominio = [("location_id.usage", "=", "internal"),
                   ("quantity", "<", 0),
                   ("company_id", "=", compania.id)]
        n = self.env["stock.quant"].search_count(dominio)
        if not n:
            return []
        return [{
            "clave": regla.codigo,
            "sujeto": "%s lineas de inventario en negativo" % n,
            "cantidad": n,
            "modelo": "stock.quant",
            "dominio": dominio,
        }]

    @api.model
    def _regla_cuenta_inv_acreedora(self, regla, compania):
        """Cuentas de inventario con saldo acreedor.

        Un activo con saldo acreedor no describe ninguna realidad fisica: no
        se puede deber inventario. Las cuentas salen de la configuracion de
        las categorias de producto, no de codigos escritos en el codigo, para
        que la regla siga funcionando si manana se separa una cuenta.
        """
        cuentas = self.env["product.category"].search([]).mapped(
            "property_stock_valuation_account_id")
        cfg = self.env["ags.config"].get_config(compania)
        cuentas |= cfg.cuenta_inventario_ids
        cuentas = cuentas.filtered(lambda c: c)
        if not cuentas:
            return []

        grupos = self.env["account.move.line"]._read_group(
            [("account_id", "in", cuentas.ids),
             ("parent_state", "=", "posted"),
             ("company_id", "=", compania.id)],
            ["account_id"], ["balance:sum"])
        salida = []
        for cuenta, balance in grupos:
            if balance >= 0:
                continue
            nombre = self._cuentas_en_idioma(cuenta).name or cuenta.code
            salida.append({
                "clave": "%s:%s" % (regla.codigo, cuenta.id),
                "sujeto": "%s %s con saldo acreedor de %s" % (
                    cuenta.code, nombre, "{:,.2f}".format(abs(balance))),
                "cantidad": 1,
                "modelo": "account.move.line",
                "dominio": [("account_id", "=", cuenta.id),
                            ("parent_state", "=", "posted"),
                            ("company_id", "=", compania.id)],
            })
        return salida
