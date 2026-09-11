# Desempeño de Clientes — AG Supply

Addon para Odoo 18 Enterprise. Evalúa la cartera de clientes en términos
cuantitativos, levanta alertas accionables y envía los reportes en PDF/HTML a
contactos y vendedores que no tienen acceso a Odoo.

---

## Instalación — Odoo.sh

Esta base corre en **Odoo.sh** (`agsupply.odoo.com`; los logs del servidor
muestran `/home/odoo/src/odoo/...`, la firma de la plataforma). Ahí no se
copian carpetas al servidor: **se hace push al repositorio Git del proyecto** y
Odoo.sh reconstruye solo. Es el mismo camino por el que ya entraron
`ag_payroll_rd` y `module_agsupply`.

1. Clonar el repositorio del proyecto Odoo.sh (o abrirlo en el editor web de
   Odoo.sh, que da una terminal sobre `/home/odoo/src/user/`).
2. Colocar la carpeta `ag_desempeno_clientes/` **en la raíz del repositorio**,
   al mismo nivel que `ag_payroll_rd/` — es decir, en el servidor queda como
   `/home/odoo/src/user/ag_desempeno_clientes/`. Si el repositorio agrupa los
   módulos en una subcarpeta de addons, va donde estén los demás.
3. `git add`, `git commit`, `git push` a la rama correspondiente.
   **Recomendado:** hacer el push primero a la rama *staging*, verificar ahí, y
   sólo entonces mergear a *production*.
4. Odoo.sh reconstruye la base y deja el módulo disponible.
5. En Odoo: **modo desarrollador** → Aplicaciones → *Actualizar lista de
   aplicaciones* → buscar «Desempeño de Clientes» → Instalar.
6. Asignar el grupo **Desempeño / Gerente** a Henry y **Desempeño / Vendedor**
   al resto de la fuerza de ventas (Ajustes → Usuarios).
7. Menú **Desempeño → Configuración → Pesos y umbrales** → botón *Recalcular
   toda la cartera*. La primera corrida sobre 544 clientes toma entre uno y
   tres minutos.

> Si no hay acceso al repositorio Git, la vía alterna es subir el ZIP desde el
> editor de Odoo.sh y descomprimirlo en `/home/odoo/src/user/` — pero ese
> cambio se pierde en la siguiente reconstrucción si no se commitea.

Sin dependencias externas: sólo módulos estándar (`sale_management`,
`account`, `stock`, `mail`, `portal`).

---

## Qué hace

### Cuatro capas, deliberadamente separadas

| Capa | Dónde vive | Qué es |
|---|---|---|
| **Ficha** | pestaña *Desempeño* del contacto | Estado actual: pipeline, backlog, crédito disponible, CxC, compras, entregas |
| **Score** | campo `ag_desemp_score` + grado A/B/C/D | Evaluación 0–100 por percentil dentro del segmento |
| **Alertas** | modelo `ag.desempeno.alerta` | Condiciones accionables, independientes del score |
| **Distribución** | wizard *Enviar reporte* | PDF/HTML por correo y WhatsApp |

No se mezclan a propósito: «orden promedio RD$ 45,000» describe, no califica.
Un almacenista de ticket bajo y alta frecuencia puede valer más que uno de
ticket alto y errático.

### El score

Cinco dimensiones, pesos configurables que deben sumar 100:

| Dimensión | Peso | Qué mide |
|---|---|---|
| Valor | 20% | Ventas netas 12M, percentil en el segmento, participación |
| Rentabilidad | 25% | Margen %, margen RD$, descuento otorgado |
| Comportamiento de pago | 25% | Días de cobro reales vs pactados, % a tiempo, vencido/total, uso de límite |
| Consistencia | 20% | Frecuencia, meses activos, recencia, variabilidad |
| Calidad operativa | 10% | Devoluciones, entregas completas |

Cada sub-score es el **percentil dentro de `ag_segmento_categoria`**, no una
nota absoluta. Percentil y no min-max: un solo cliente enorme aplastaría la
escala de todos los demás. Un segmento con menos de 5 clientes evaluables cae
a la cartera completa antes que producir un percentil sin sentido.

Grados: A ≥ 80 · B ≥ 65 · C ≥ 45 · D < 45 · *Nuevo* si no acumula los meses
mínimos de historia.

### Tendencia con doble señal

* **Velocidad** — bloque de 3 meses contra los 3 anteriores. Reacciona rápido.
* **Dirección** — pendiente de la regresión sobre los 12 meses, normalizada
  contra el promedio mensual. Dice hacia dónde va la relación completa.

Cuando ambas discrepan el módulo marca **⚠ Divergente** en vez de inventar una
conclusión — ese es justamente el caso que merece mirada humana. ALIMUNDO SRL
es el ejemplo real: dirección +5.9% (la relación creció todo el año) contra
velocidad −58.4% (el último trimestre se desplomó).

### Alertas

| Alerta | Condición |
|---|---|
| Crédito excedido | CxC + backlog > límite |
| **Deterioro combinado** | Caída > 20% **y** días de pago estirados > 10 |
| Caída de volumen | Velocidad bajo el umbral |
| Cliente dormido | Sin comprar 60+ días (90+ = crítica) |
| Margen erosionado | Margen < 10% |
| **Margen fuera de rango** | Margen > 70% — casi siempre costo mal cargado |
| Backlog estancado | Orden abierta > 15 días |
| Entregado sin facturar | `invoice_status = to invoice` |
| Vencido crítico | 60+ días de atraso, o crédito Suspendido/Legal |
| Concentración | Cliente > 8% de la facturación total |

La combinada es la más valiosa: cada mitad por separado es ruido; juntas
anticipan fuga a competencia o problema financiero meses antes de que aparezca
en el aging.

Las alertas que dejan de cumplirse se archivan (`activa = False`), no se
borran: el historial es parte de la conversación con el cliente. La nota de
seguimiento que escriba el vendedor sobrevive a los recálculos.

---

## Los tres reportes

| Reporte | Para quién | Qué lleva |
|---|---|---|
| **Ficha de desempeño** | vendedor, gerencia | Grado, serie de 12 meses, CxC, pipeline, alertas, composición del score |
| **Cartera** | gerencia, o filtrada al vendedor | Tabla con franja de severidad al borde, grado y tendencia |
| **Estado de cuenta** | el cliente | Facturas, saldos, antigüedad. **Nada más** |

**El cliente nunca recibe su grado ni su posición en el ranking.** El wizard lo
impide con un `UserError`, no con una advertencia: la calificación es interna y
si se filtra, la conversación deja de ser comercial.

HTML y PDF salen de la misma plantilla QWeb. Dos plantillas paralelas terminan
desincronizadas en tres meses.

---

## Distribución

**La vía de envío es la misma que ya usan los crones de AG Supply:**
`env['mail.mail'].create({...}).send()`, sobre el servidor de correo que
provee Odoo.sh (`mail.catchall.domain = agsupply.odoo.com`). Por eso
`ir.mail_server` está vacío y aun así los correos salen: la plataforma pone el
SMTP. No hace falta configurar nada.

El módulo también reutiliza la identidad visual de esos correos —morado
`#6c3883`, el logo de la compañía en el encabezado y el pie con la dirección de
Calle Las Palomas #60— para que el cliente y el vendedor reconozcan el correo
como de la casa y no como uno de un sistema distinto. Y respeta
`x_studio_excluir_recordatorios_automticos_1`: un cliente marcado para excluir
de recordatorios automáticos tampoco recibe el estado de cuenta del módulo.

* **Correo** — PDF adjunto más el resumen en el cuerpo, porque muchos abren
  desde el teléfono y no descargan.
* **WhatsApp** — enlace, no adjunto. Abre `wa.me` con el mensaje redactado y
  el enlace dentro; el vendedor lo dispara desde su propio número, que además
  es el que el cliente reconoce. Meta exige plantilla aprobada con encabezado
  de documento para mandar un PDF proactivo, y el archivo tendría que vivir en
  una URL pública de todos modos.
* **Página pública** — `/ag/estado-cuenta/<id>/<token>`, sin usuario de Odoo.
  El token caduca a los 30 días configurables: un enlace se reenvía por
  WhatsApp y queda circulando fuera de control.

---

## Procesos automáticos

| Cron | Frecuencia | Qué hace |
|---|---|---|
| *Desempeño: recalcular cartera* | diario 6:30 AM | Recalcula métricas, score y alertas |
| *Desempeño: cerrar mes* | día 1, 8:00 AM | Congela el snapshot del mes cerrado |

Los campos son **almacenados, no computados en tiempo real**: calcular doce
meses de facturación, conciliación y despacho para 544 clientes en cada
lectura haría inusable la vista de contactos.

---

## Decisiones que conviene conocer antes de tocar el código

1. **La ventana termina en el último mes cerrado**, no en el mes en curso. Si
   incluyera el mes corriente, cada día 2 toda la cartera aparecería en caída
   libre contra un mes que apenas empezó, y una alerta que se dispara siempre
   no sirve. La recencia, la CxC y el backlog sí miran el día de hoy.

2. **Todo se consolida por matriz** (`commercial_partner_id`). Las sucursales
   suman al cliente comercial, que es quien tiene el crédito.

3. **El crédito disponible resta el backlog no facturado.** Sin eso se
   autoriza despacho contra crédito ya comprometido.

4. **El lead time separa mostrador de entrega programada.** Un despacho
   validado dentro de 4 horas de la orden es retiro en planta; promediarlo con
   una entrega de 5 días borra la señal.

5. **El margen sobre 70% se limita al puntuar** y levanta alerta de revisión
   de costeo. Casi siempre indica costo mal cargado, y sin ese tope el módulo
   coronaría como cliente A a quien sólo tiene un error contable.

6. **Las agregaciones pesadas van en SQL**, no en el ORM: una consulta
   agrupada resuelve los 544 clientes de una vez.

---

## Limitaciones conocidas del ERP (no del módulo)

Estas se detectaron inspeccionando el Odoo productivo y **limitan lo que el
módulo puede medir hoy**:

* **`commitment_date` está vacío en el 100% de las órdenes.** No existe fecha
  comprometida al cliente, así que **OTIF no es medible**. El módulo mide el
  lead time real; para medir cumplimiento de promesa hay que empezar a llenar
  ese campo en el proceso de venta. Es la variable de mayor valor que no se
  está capturando.
* **El módulo `whatsapp` no está instalado.** Por eso la vía implementada es
  `wa.me` disparado por el vendedor. Migrar a la API de Meta (o a un
  proveedor como Twilio/360dialog) sólo cambia el método `_abrir_whatsapp`.
* **Contactos incompletos:** de 544 clientes, 277 tienen correo (50.9%), 480
  tienen teléfono (88.2%), pero sólo **6** tienen `ag_email_encargado` o
  `ag_whatsapp_encargado`. El wizard avisa antes de enviar cuántos quedarán
  fuera, pero sin poblar esos campos la distribución no opera. Empezar por los
  68 clientes «Muy importante» e «Importante».
* **Usuario duplicado:** existen dos usuarios de Odoo para la misma persona —
  id 22 «Constante Portela» e id 14 «Selyne Mendoza». Unificarlos antes de
  repartir carteras por vendedor, o la cartera de esa persona sale partida en
  dos.
* **Datos de segmentación incompletos:** 155 clientes sin
  `ag_segmento_categoria` (28.5%) y 148 sin `ag_categoria_cliente`. El módulo
  los agrupa en «sin segmento», pero el percentil pierde sentido comparativo.
* **Límites de crédito inconsistentes:** 68 clientes con crédito «Cerrado»
  conservan RD$ 1,950,010 de límite asignado, y 220 clientes no tienen
  `ag_estado_de_credito`.

---

## Estructura

```
ag_desempeno_clientes/
├── models/
│   ├── ag_desempeno_config.py     pesos, cortes y umbrales
│   ├── ag_desempeno_snapshot.py   foto mensual congelada
│   ├── ag_desempeno_alerta.py     alertas con seguimiento
│   └── res_partner.py             motor de cálculo (SQL + score)
├── wizard/ag_envio_reporte.py     correo, WhatsApp, descarga
├── controllers/main.py            página pública con token
├── report/                        QWeb: ficha, cartera, estado de cuenta
├── views/                         pestaña, cartera, alertas, historial
├── security/                      grupos Vendedor / Gerente + reglas
└── data/                          config inicial, plantillas, crones
```
