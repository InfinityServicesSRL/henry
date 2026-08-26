/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { _t } from "@web/core/l10n/translation";

/**
 * Cockpit de Gerencia.
 *
 * Tres zonas y el orden importa:
 *
 *   0. Banda de confianza: que tan creible es lo que sigue. Va primero
 *      porque un margen calculado sobre inventario negativo no es un
 *      margen, y el gerente tiene derecho a saberlo antes de leer la cifra.
 *   1. Excepciones: solo lo que requiere atencion.
 *   2. Panorama: ventas del mes e indicadores ejecutivos.
 *
 * El cockpit se abre para saber si hay algo que atender, no para admirar
 * los indicadores. Cuando no hay excepciones la zona se colapsa a una sola
 * linea que lo dice: una pantalla vacia que informa es mas util que un
 * espacio en blanco.
 */
export class AgsCockpit extends Component {
    static template = "ags_intelligence.Cockpit";
    // Layout monta el control panel nativo: breadcrumb, migas de navegacion
    // y la barra de acciones en la misma fila donde cualquier usuario de
    // Odoo espera encontrarlas. Antes el cockpit dibujaba su propia
    // cabecera, y era lo primero que delataba que no era una vista nativa.
    static components = { Layout };
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            cargando: true,
            recalculando: false,
            datos: null,
            mostrarExcepciones: true,
            mostrarBanda: false,
            mostrarFiltros: false,
            bloquesAbiertos: {},
            series: {},
            serieAbierta: false,
            generandoAlertas: false,
            desgloses: {},
            desgloseAbierto: false,
            filtros: {},
            opciones: { vendedores: [], mercados: [], almacenes: [] },
            mes: 0,
        });
        onWillStart(async () => {
            await this.cargarOpciones();
            await this.cargar();
        });
    }

    async cargar() {
        this.state.cargando = true;
        try {
            const fecha = this.fechaConsulta();
            this.state.datos = await this.orm.call(
                "ags.cockpit", "datos", [fecha, this.filtrosPlanos()]);
            // El detalle de la banda se abre solo cuando hay algo grave.
            // En nivel aviso el titular alcanza; abrirlo siempre convertiria
            // la advertencia en ruido de fondo que se aprende a ignorar.
            this.state.mostrarBanda =
                this.state.datos.confianza &&
                this.state.datos.confianza.nivel === "alerta";
        } finally {
            this.state.cargando = false;
        }
    }

    fechaConsulta() {
        if (!this.state.mes) {
            return false;
        }
        const d = new Date();
        d.setDate(1);
        d.setMonth(d.getMonth() - this.state.mes);
        return d.toISOString().slice(0, 10);
    }

    async recalcular() {
        this.state.recalculando = true;
        try {
            this.state.datos = await this.orm.call(
                "ags.cockpit", "recalcular",
                [this.fechaConsulta(), this.filtrosPlanos()]);
            this.notification.add(_t("Periodo recalculado"), { type: "success" });
        } catch (e) {
            this.notification.add(_t("No se pudo recalcular el periodo"),
                { type: "danger" });
            throw e;
        } finally {
            this.state.recalculando = false;
        }
    }

    async mover(delta) {
        const nuevo = this.state.mes + delta;
        if (nuevo < 0 || nuevo > 24) {
            return;
        }
        this.state.mes = nuevo;
        await this.cargar();
    }

    abrir(item) {
        const mapa = {
            parametro: ["ags.parametro", _t("Parametro")],
            meta: ["ags.meta", _t("Meta")],
            rentabilidad: ["ags.rentabilidad", _t("Rentabilidad")],
        };
        const destino = mapa[item.accion];
        if (!destino || !item.res_id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: destino[0],
            res_id: item.res_id,
            views: [[false, "form"]],
            target: "current",
            name: destino[1],
        });
    }

    abrirParametro(tarjeta) {
        if (!tarjeta.parametro_id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "ags.parametro",
            res_id: tarjeta.parametro_id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    /**
     * Abre los registros que ensucian el dato.
     *
     * El servidor manda el dominio junto al conteo justamente para esto: una
     * advertencia que no lleva al lugar donde se arregla el problema se
     * termina ignorando.
     */
    abrirHallazgo(hallazgo) {
        if (!hallazgo.cantidad || !hallazgo.modelo) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: hallazgo.modelo,
            domain: hallazgo.dominio,
            views: [[false, "list"], [false, "form"]],
            target: "current",
            name: hallazgo.etiqueta,
        });
    }

    async cargarOpciones() {
        this.state.opciones = await this.orm.call(
            "ags.cockpit", "opciones_filtros", []);
    }

    /** useState envuelve el objeto en un Proxy; el ORM necesita uno plano. */
    filtrosPlanos() {
        return Object.assign({}, this.state.filtros);
    }

    async cambiarFiltro(clave, valor) {
        const id = parseInt(valor, 10);
        if (id) {
            this.state.filtros[clave] = id;
        } else {
            delete this.state.filtros[clave];
        }
        await this.cargar();
    }

    async quitarFiltro(clave) {
        delete this.state.filtros[clave];
        await this.cargar();
    }

    async limpiarFiltros() {
        this.state.filtros = {};
        await this.cargar();
    }

    alternarBloque(eje) {
        this.state.bloquesAbiertos[eje] = !this.state.bloquesAbiertos[eje];
    }

    estaAbierto(eje) {
        return !!this.state.bloquesAbiertos[eje];
    }

    /**
     * Que filas se ven en un bloque cerrado.
     *
     * Un bloque cerrado no se calla del todo: sigue mostrando lo que esta en
     * rojo. Colapsar por completo un eje con cinco indicadores fuera de rango
     * convertiria el plegado en una forma de esconder el problema, que es
     * justo lo contrario de lo que hace este cockpit.
     */
    filasVisibles(bloque) {
        if (this.estaAbierto(bloque.eje)) {
            return bloque.filas;
        }
        return bloque.filas.filter((f) => f.semaforo === "rojo");
    }

    claseDelta(delta) {
        if (!delta || !delta.hay_dato) {
            return "text-muted";
        }
        return {
            mejora: "text-success",
            deterioro: "text-danger",
        }[delta.sentido] || "text-muted";
    }

    /**
     * Abre o cierra la serie de un indicador.
     *
     * Se carga bajo demanda y se cachea: veinticuatro meses por cada uno de
     * los diez indicadores serian doscientas cuarenta lecturas para una
     * pantalla donde el gerente normalmente mira una o dos.
     */
    async alternarSerie(codigo) {
        if (this.state.serieAbierta === codigo) {
            this.state.serieAbierta = false;
            return;
        }
        if (!this.state.series[codigo]) {
            this.state.series[codigo] = await this.orm.call(
                "ags.cockpit", "serie", [codigo, 24, this.fechaConsulta()]);
        }
        this.state.serieAbierta = codigo;
        // Aunque el panel se dibuja junto a su eje, la tarjeta pulsada puede
        // ser de la ultima fila y el grafico nacer justo debajo del borde
        // visible. Dos frames de espera: uno para que OWL renderice, otro
        // para que el navegador calcule la posicion definitiva.
        requestAnimationFrame(() => requestAnimationFrame(() => {
            const el = document.querySelector(".o_ags_serie");
            if (el) {
                el.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }
        }));
    }

    /** El panel pertenece al eje de la tarjeta que lo abrio. */
    serieEnEje(grupo) {
        return grupo.tarjetas.some((t) => t.codigo === this.state.serieAbierta);
    }

    get serieActual() {
        const c = this.state.serieAbierta;
        return c ? this.state.series[c] : null;
    }

    /**
     * Convierte la serie en coordenadas SVG.
     *
     * Se dibuja a mano en vez de usar una libreria de graficos por dos
     * razones: la unica forma que hace falta es una linea sobre bandas de
     * fondo, y las rutas de las librerias que trae Odoo cambian entre
     * versiones, lo que convertiria el cockpit en algo que se rompe solo en
     * la proxima actualizacion.
     */
    geometriaSerie(s) {
        const W = 640, H = 160, PX = 10, PY = 12;
        if (!s || !s.puntos || !s.puntos.length) {
            return null;
        }
        const valores = s.puntos.map((p) => p.valor);
        const refs = [];
        if (s.banda.hay_dato) {
            refs.push(s.banda.minimo, s.banda.objetivo, s.banda.clase_mundial);
        }
        if (s.baseline.hay_dato) {
            refs.push(s.baseline.valor);
        }
        const todos = valores.concat(refs.filter((v) => v || v === 0));
        let min = Math.min(...todos);
        let max = Math.max(...todos);
        if (min === max) {
            min -= 1;
            max += 1;
        }
        const margen = (max - min) * 0.08;
        min -= margen;
        max += margen;

        const x = (i) => s.puntos.length === 1
            ? W / 2
            : PX + (i * (W - 2 * PX)) / (s.puntos.length - 1);
        const y = (v) => H - PY - ((v - min) / (max - min)) * (H - 2 * PY);
        const rect = (a, b) => {
            const y1 = y(a);
            const y2 = y(b);
            return { y: Math.min(y1, y2), alto: Math.abs(y2 - y1) };
        };

        return {
            ancho: W,
            alto: H,
            linea: s.puntos.map((p, i) => `${x(i)},${y(p.valor)}`).join(" "),
            marcas: s.puntos.map((p, i) => ({
                cx: x(i), cy: y(p.valor), semaforo: p.semaforo,
                atipico: p.atipico, label: p.label, texto: p.texto,
            })),
            zonaBuena: s.banda.hay_dato && s.banda.clase_mundial
                ? rect(s.banda.objetivo, s.banda.clase_mundial) : null,
            zonaTolerable: s.banda.hay_dato && s.banda.minimo
                ? rect(s.banda.minimo, s.banda.objetivo) : null,
            yObjetivo: s.banda.hay_dato ? y(s.banda.objetivo) : null,
            yBaseline: s.baseline.hay_dato ? y(s.baseline.valor) : null,
            primerLabel: s.puntos[0].label,
            ultimoLabel: s.puntos[s.puntos.length - 1].label,
        };
    }

    claseMarca(m) {
        if (m.atipico) {
            return "o_ags_marca_atipica";
        }
        return {
            verde: "o_ags_marca_verde",
            amarillo: "o_ags_marca_amarilla",
            rojo: "o_ags_marca_roja",
        }[m.semaforo] || "o_ags_marca_neutra";
    }

    get hayFiltro() {
        return Object.keys(this.state.filtros).length > 0;
    }

    async cerrarAlerta(alerta, accion) {
        this.state.datos = await this.orm.call(
            "ags.cockpit", "cerrar_alerta",
            [alerta.id, accion, this.fechaConsulta(), this.filtrosPlanos()]);
        this.notification.add(
            accion === "atendida" ? _t("Alerta atendida") : _t("Alerta descartada"),
            { type: "success" });
    }

    async generarAlertas() {
        this.state.generandoAlertas = true;
        try {
            this.state.datos = await this.orm.call(
                "ags.cockpit", "generar_alertas",
                [this.fechaConsulta(), this.filtrosPlanos()]);
            const n = this.state.datos.alertas.length;
            this.notification.add(
                n ? _t("%s alertas del periodo", n) : _t("Sin alertas que reportar"),
                { type: n ? "warning" : "success" });
        } finally {
            this.state.generandoAlertas = false;
        }
    }

    abrirDestinoAlerta(alerta) {
        if (!alerta.modelo_destino || !alerta.res_id_destino) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: alerta.modelo_destino,
            res_id: alerta.res_id_destino,
            views: [[false, "form"]],
            target: "current",
            name: alerta.nombre_destino,
        });
    }

    clasePrioridad(p) {
        return {
            "1": "o_ags_danger",
            "2": "o_ags_warning",
        }[p] || "o_ags_info";
    }

    /**
     * Abre el desglose de un indicador.
     *
     * Es la peticion del auditor: desde la cifra, llegar a las cuentas que
     * la forman y de ahi a los apuntes. Se carga bajo demanda y se cachea
     * como las series.
     */
    async alternarDesglose(codigo) {
        if (this.state.desgloseAbierto === codigo) {
            this.state.desgloseAbierto = false;
            return;
        }
        if (!this.state.desgloses[codigo]) {
            this.state.desgloses[codigo] = await this.orm.call(
                "ags.cockpit", "desglose", [codigo, this.fechaConsulta()]);
        }
        this.state.desgloseAbierto = codigo;
    }

    get desgloseActual() {
        const c = this.state.desgloseAbierto;
        return c ? this.state.desgloses[c] : null;
    }

    /** El servidor decide a que registros lleva cada componente. */
    async abrirOrigen(componente) {
        if (!componente.tiene_origen) {
            return;
        }
        const accion = await this.orm.call(
            "ags.cockpit", "abrir_origen", [componente.id]);
        if (accion) {
            this.action.doAction(accion);
        }
    }

    get confianza() {
        return this.state.datos ? this.state.datos.confianza : null;
    }

    get hallazgosSucios() {
        const c = this.confianza;
        if (!c) {
            return [];
        }
        return c.hallazgos.filter((h) => h.cantidad);
    }

    /** Agrupa las tarjetas por eje para que el bloque se lea por bloques. */
    get porEje() {
        const grupos = [];
        if (!this.state.datos) {
            return grupos;
        }
        for (const t of this.state.datos.ejecutivo) {
            let g = grupos.find((x) => x.eje === t.eje);
            if (!g) {
                g = { eje: t.eje, nombre: t.eje_nombre, tarjetas: [] };
                grupos.push(g);
            }
            g.tarjetas.push(t);
        }
        return grupos;
    }

    claseBanda(nivel) {
        return {
            alerta: "o_ags_banda_alerta",
            aviso: "o_ags_banda_aviso",
        }[nivel] || "o_ags_banda_ok";
    }

    iconoBanda(nivel) {
        return {
            alerta: "fa-exclamation-triangle",
            aviso: "fa-exclamation-circle",
        }[nivel] || "fa-check-circle";
    }

    claseSemaforo(s) {
        return {
            verde: "text-bg-success",
            amarillo: "text-bg-warning",
            rojo: "text-bg-danger",
        }[s] || "text-bg-secondary";
    }

    claseTendencia(t) {
        return {
            mejora: "text-success",
            deterioro: "text-danger",
        }[t] || "text-muted";
    }

    iconoTendencia(t) {
        return {
            mejora: "fa-arrow-up",
            deterioro: "fa-arrow-down",
            sube: "fa-arrow-up",
            baja: "fa-arrow-down",
            plana: "fa-minus",
        }[t] || "fa-minus";
    }
}

registry.category("actions").add("ags_cockpit", AgsCockpit);
