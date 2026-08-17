/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

/**
 * Cockpit de Gerencia.
 *
 * Dos zonas y el orden importa: primero las excepciones, despues el
 * panorama. El cockpit se abre para saber si hay algo que atender, no
 * para admirar los indicadores.
 *
 * Cuando no hay excepciones la zona se colapsa a una sola linea que lo
 * dice. Una pantalla vacia que informa es mas util que un espacio en
 * blanco.
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
            mes: 0,
        });
        onWillStart(() => this.cargar());
    }

    async cargar() {
        this.state.cargando = true;
        try {
            const fecha = this.fechaConsulta();
            this.state.datos = await this.orm.call(
                "ags.cockpit", "datos", [fecha]);
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
                "ags.cockpit", "recalcular", [this.fechaConsulta()]);
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
