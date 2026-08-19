/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
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

    get hayFiltro() {
        return Object.keys(this.state.filtros).length > 0;
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
