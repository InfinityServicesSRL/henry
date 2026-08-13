# AG Intelligence — Fase 2A: Sistema de Parámetros

## Qué contiene esta entrega

Siete modelos nuevos que convierten el módulo en un sistema de medición
con memoria: sabe dónde arrancó, contra qué se compara y cómo va evolucionando.

| Modelo | Rol |
|---|---|
| `ags.parametro` | Catálogo maestro: **qué** se mide |
| `ags.baseline` | Punto de partida **congelado e inmutable** |
| `ags.benchmark` | Referencia de mercado **versionada**, con fuente |
| `ags.medicion` | Serie temporal real desde Odoo |
| `ags.estacionalidad` | Factores por mes, calculados del histórico propio |
| `ags.proyeccion` | Proyecciones auditables contra el real |
| `ags.fuente` | Trazabilidad de cada investigación |

## Tres decisiones de diseño que conviene entender

**1. El baseline se congela y no se toca.**
Si se recalculara con cada consulta se perdería la capacidad de medir mejora,
que es justamente el propósito. El modelo bloquea la escritura a nivel de
código, no solo por convención. Cuando se limpien los datos del ERP se crea
una v2 y la v1 queda visible: la distancia entre ambas revela cuánto estaba
distorsionando la data sucia.

**2. Bandas, no números únicos.**
Cada benchmark guarda mínimo aceptable, objetivo y clase mundial. Un solo
número no distingue entre "aceptable" y "excelente". El campo `direccion`
del parámetro invierte automáticamente la lógica del semáforo en variables
donde menor es mejor (merma, DSO, tasa de interés).

**3. Los tipo C se cargan vacíos a propósito.**
De 32 parámetros, 17 no tienen benchmark externo porque no existe dato
público confiable para un convertidor de tissue dominicano de esta escala.
Esos se comparan solo contra el baseline propio. Un vacío honesto es
preferible a un número inventado cuando el valor se congela como referencia
en un sistema de gestión real.

## Estado de la carga

- **32 parámetros** en 9 secciones
- **13 fuentes** documentadas con fecha y confiabilidad
- **12 benchmarks** cargados (tipos A y B)
- **3 parámetros macro** sin banda por ser contexto, no meta

## Ajustes documentados en los benchmarks

Dos casos donde el dato externo NO se copió tal cual:

**Margen bruto** — Softys (33.2%) y Kimberly-Clark México (40.9%) son
fabricantes integrados que producen su propia bobina. AG Supply compra la
bobina ya hecha, así que no captura ese margen. Las bandas se ajustaron a
la baja: 15 / 22 / 28.

**OEE** — las referencias publicadas vienen de medición automática. AG Supply
calcula desde registros manuales de OT, que sobreestiman 8–12 puntos. Las
bandas se cargaron ~7 puntos por encima de la referencia externa para que la
comparación sea válida. Si algún día se instala captura automática, hay que
recalibrarlas hacia abajo.

Ambos ajustes están escritos en el campo `ajuste_aplicado` del registro, no
solo aquí.

## Correcciones de Fase 1 incluidas

- `web_icon` en el menú raíz + `static/description/icon.png` (140×140)
- Auto-asignación del grupo al administrador en la instalación
- Nota de diseño sobre granularidad de permisos para Fase 2B

## Instalación

El archivo `views/ags_welcome_views.xml` de la Fase 1 **no se incluye**:
consérvalo tal como está en el repo.

1. Copiar estos archivos sobre `ags_intelligence/` en el repo `henry`, rama 18.0
2. Commit y push
3. Actualizar el puntero del submódulo en `ag_supply` rama `stg`
4. Rebuild de staging
5. Actualizar el módulo desde Aplicaciones

## Antes de encender el cron

Se instala **desactivado** a propósito. Secuencia:

1. Revisar el catálogo de parámetros y ajustar responsables
2. Calcular el baseline de cada parámetro
3. Marcar honestamente la calidad del dato (verificado / parcial / sucio)
4. Congelar
5. Recién entonces activar el cron

Si el cron empieza a generar mediciones sin baseline, las comparaciones salen
vacías y se acumula ruido en la serie.

## Lo que queda pendiente

- **Calculadores 2B**: margen bruto, merma, costo de MP sobre ventas
- **Calculadores 2C**: DSO, DIO, CCC, cartera corriente
- **Estacionalidad**: el método `recalcular_desde_historico()` está definido
  pero sin implementar. Los factores del mercado dominicano de tissue
  (temporada escolar, fin de año, Semana Santa, ciclo quincenal) hay que
  **medirlos** desde las ventas reales, no suponerlos.
- **Granularidad de permisos**: hoy un solo grupo ve todo, incluidos márgenes
  por cliente y P&L.
