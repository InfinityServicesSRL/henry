# Despliegue a staging — paso a paso

Ambiente: **Odoo.sh**, proyecto `agsupply`. Los módulos custom viven en el
repositorio Git del proyecto (`/home/odoo/src/user/` en el servidor).

---

## Antes de empezar

- [ ] Tener acceso al repositorio Git del proyecto Odoo.sh
- [ ] Confirmar que existe una rama **staging**. Si no existe, crearla desde
      producción en el panel de Odoo.sh (botón *Fork* sobre la rama production)
- [ ] Verificar que la rama staging tenga una copia reciente de la base de
      producción — sin datos reales las pruebas no dicen nada

---

## 1 · Subir el módulo (5 minutos)

```bash
# En su copia local del repositorio
git checkout staging
git pull

# Copiar la carpeta del módulo a la raíz del repo,
# al mismo nivel que ag_payroll_rd/
cp -r ~/Descargas/ag_desempeno_clientes .

git add ag_desempeno_clientes
git commit -m "Módulo de desempeño de clientes — primera versión para pruebas"
git push origin staging
```

Odoo.sh reconstruye la rama sola. En el panel se ve el progreso; toma entre
tres y diez minutos. **Si la construcción falla, el log dice en qué archivo y
en qué línea** — mándemelo y lo corrijo.

---

## 2 · Instalar (2 minutos)

1. Abrir la URL de staging que da Odoo.sh
2. Activar el **modo desarrollador**: Ajustes → al final de la página →
   *Activar el modo desarrollador*
3. Aplicaciones → menú ⋮ → **Actualizar lista de aplicaciones**
4. Quitar el filtro «Aplicaciones» de la barra de búsqueda
5. Buscar **Desempeño de Clientes** → *Activar*

Si la instalación falla, el error sale en pantalla con el nombre del campo o
la vista que lo causó.

---

## 3 · Dar permisos (1 minuto)

Ajustes → Usuarios y compañías → Usuarios:

| Usuario | Grupo a marcar |
|---|---|
| Henry Santana | Desempeño de Clientes → **Gerente** |
| Gabriel Segura, Paola Tavares, Constante Portela, Solanyi Quezada, Judith Compres | Desempeño de Clientes → **Vendedor** |
| Martín Almonte | **Gerente** (ve toda la cartera para cobros) |

---

## 4 · Sembrar los datos de prueba (3 minutos)

Sin esto el módulo se ve vacío la primera vez: no hay rutas nuevas, ni
potenciales, ni perfiles.

En el panel de Odoo.sh, rama staging → pestaña **Shell**:

```python
exec(open('/home/odoo/src/user/ag_desempeno_clientes/tools/datos_prueba.py').read())
```

El script hace cuatro cosas e imprime el resultado de cada una:

1. Crea las **22 rutas geográficas** con zona, distancia y costo sugerido
2. Asigna ruta por ciudad a los clientes que no la tienen (resuelve la mayoría
   de los 268 sin ruta)
3. Hereda distancia y costo logístico desde la ruta
4. Pone potencial y perfil de ejemplo en los diez clientes mayores, marcados
   como `[DATO DE PRUEBA]`

Después recalcula toda la cartera y guarda el snapshot del mes cerrado.

> **Nunca correr este script en producción.** Sobrescribe rutas y campos de
> clientes reales.

Si prefiere no sembrar nada, el recálculo solo se lanza desde
Desempeño → Configuración → *Recalcular toda la cartera*.

---

## 5 · Qué revisar (15 minutos)

### La cartera
- [ ] Desempeño → **Todos los clientes** muestra los 544 con grado y tendencia
- [ ] Los filtros **Vendedor** y **Ruta** dividen la lista correctamente
- [ ] Filtro *Requieren acción esta semana* devuelve algo razonable
- [ ] Los totales al pie de las columnas cuadran

### Una ficha
- [ ] Abrir ALIMUNDO SRL → pestaña **Desempeño**
- [ ] Compra mensual ≈ RD$ 917,690 y venta 12M ≈ RD$ 11,012,278
- [ ] Margen 44.7% · DSO ≈ 45.6 días · crédito disponible ≈ RD$ 1,929,420
- [ ] Tendencia dice **⚠ Divergente** (velocidad −58%, dirección +5.9%)
- [ ] El botón *Recalcular ahora* funciona y no tarda más de unos segundos

### Los reportes
- [ ] Imprimir → **Ficha de desempeño** genera el PDF con el gráfico de barras
- [ ] Seleccionar varios clientes en la lista → Imprimir → **Cartera**
- [ ] Imprimir → **Estado de cuenta**: verificar que **no** aparezca grado ni score

### El envío
- [ ] Acción *Enviar reporte* → tipo Estado de cuenta → canal Correo →
      **Previsualizar** antes de enviar
- [ ] Probar un envío real a su propio correo
- [ ] Verificar que el saludo diga el nombre del **vendedor asignado**, no el suyo
- [ ] Canal WhatsApp: debe abrir wa.me con el mensaje redactado
- [ ] Intentar enviar una *Ficha de desempeño* al contacto del cliente:
      **tiene que bloquearlo con un error**

### El enlace público
- [ ] En la ficha, generar el estado de cuenta y abrir el enlace
      `/ag/estado-cuenta/<id>/<token>` en una ventana de incógnito
- [ ] Debe verse sin pedir usuario, y **sin** grado ni comparaciones

---

## 6 · Rendimiento

El recálculo completo toca 544 clientes con consultas sobre doce meses de
facturación, conciliación y despacho.

- [ ] Cronometrar *Recalcular toda la cartera*. Referencia esperada: **uno a
      tres minutos**
- [ ] Si pasa de cinco minutos, avisarme: hay que revisar índices

Los dos crones quedan programados solos (recálculo diario 6:30 AM, cierre
mensual día 1). En staging conviene **desactivarlos** para que no corran
mientras se prueba: Ajustes → Técnico → Acciones planificadas.

---

## 7 · Qué reportarme

Para cada problema, con esto lo corrijo sin ida y vuelta:

1. **Qué hizo** (el menú, el botón, el cliente)
2. **Qué esperaba** y **qué pasó**
3. Si salió un error rojo: la traza completa (botón *Ver detalles*)
4. Si es visual: captura de pantalla

---

## Después de staging

Cuando todo pase limpio, el paso a producción es el mismo push a la rama
`production`. Antes de eso conviene decidir:

- Si se sube con los datos de prueba borrados o si se rehace la siembra de
  rutas en producción (las rutas sí valen; el potencial y los perfiles de
  ejemplo hay que borrarlos)
- Si los crones arrancan activos o se dejan apagados la primera semana
- Quién revisa las rutas asignadas automáticamente antes de que la gerencia
  empiece a repartir cartera por ellas
