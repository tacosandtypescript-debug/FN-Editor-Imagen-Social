# EditImg Dashboard

**Radar de publicaciones.** Trae lo que publican las cuentas de X que sigas y lo
enseña para revisarlo: imágenes y vídeos, con acciones rápidas por publicación.

```
Cuentas de X → sondeo automático → publicaciones nuevas → dashboard
   → Abrir en X · Copiar enlace · Conservar · Enviar a Telegram (Fase 2)
```

Cada publicación se puede **abrir en X**, **copiar su enlace** o **conservar**.
Conservarla es lo que la salva de la limpieza automática. El botón de Telegram
queda preparado en la bandeja, pero permanece desactivado hasta conectar el
adaptador que recibe el enlace y devuelve la tarjeta.

> Este dashboard **no genera tarjetas ni muestra un editor**. El compositor sigue
> en `bin/` del repositorio, intacto; la conexión que enviará un enlace a
> Telegram y devolverá la tarjeta queda separada como la siguiente fase.

---

## Arranque rápido

```powershell
cd "ruta\al\FN-Editor-Imagen-Social"
python -m dashboard
```

Abre <http://127.0.0.1:8765/>.

Opciones:

```powershell
python -m dashboard --lan          # accesible desde el móvil en la misma red
python -m dashboard --port 9000    # otro puerto
python -m dashboard --no-poller    # sin sondeo periódico (el botón sigue activo)
```

El núcleo funciona **solo con la biblioteca estándar y Pillow**, así que
`requirements.txt` no se toca.

---

## Qué se reutiliza (y qué no se toca)

| Pieza | Origen | Cómo se usa |
|---|---|---|
| Lectura del post | `bin/fetch_media.py` + `dashboard/posts.py` | Se carga por ruta, sin modificarlo: da texto, medios, fecha y señal de vídeo |
| Miniaturas | CDN de X | Se pide la variante de 360 px en lugar del original |

**No se ha modificado** ningún archivo de `bin/`, los presets, las fuentes, las
skills ni los tests originales del repositorio. El compositor de tarjetas sigue
ahí y sus tests siguen pasando; este dashboard simplemente ya no lo llama.

---

## Módulos

```
dashboard/
  config.py              ajustes, rutas y lectura de .env
  store.py               SQLite: cuentas y publicaciones (con su estado)
  service.py             lógica del visor (sin HTTP, testeable)
  server.py              servidor HTTP y API JSON (biblioteca estándar)
  poller.py              sondeo automático en segundo plano
  posts.py               lee texto, medios y fecha real de una publicación
  providers/
    base.py              interfaces comunes
    timelines.py         descubrimiento: navegador / Nitter / X API
  web/                   interfaz (HTML + CSS + JS sin dependencias)
```

Cada capacidad vive detrás de una interfaz propia: cambiar la forma de descubrir
publicaciones no toca el almacén ni la interfaz.

---

## Proveedores

### Descubrimiento de publicaciones

| Proveedor | Requiere | Estado |
|---|---|---|
| `browser` | Playwright + Chrome (visible) | Por defecto. Lee x.com con tu navegador |
| `nitter` | nada | Respaldo sin dependencias. Instancias públicas, inestables |
| `xapi` | token de pago | API v2 oficial |

Se intentan **en cadena**: si el elegido falla, prueba el siguiente y lo deja
anotado en la bitácora. Verificado en la práctica: de 24 cuentas, 23 se leyeron
por Nitter y una por navegador, sin ningún fallo.

> **Modo oculto:** x.com rechaza Chrome en `headless`. Hay que dejarlo visible
> (`DASHBOARD_BROWSER_HEADLESS=0`), que además permite iniciar sesión una vez y
> que el perfil la recuerde.

## Configuración

Copia `dashboard/.env.example` a `dashboard/.env` y ajusta lo que necesites.
Ese archivo está ignorado por Git: **ninguna credencial se sube al
repositorio**.

Variables más útiles:

| Variable | Por defecto | Para qué |
|---|---|---|
| `DASHBOARD_PORT` | `8765` | Puerto del dashboard |
| `DASHBOARD_TIMELINE_PROVIDER` | `browser` | `browser`, `nitter` o `xapi` |
| `DASHBOARD_POLL_INTERVAL` | `900` | Segundos entre sondeos (mínimo 60) |
| `DASHBOARD_POLL_ON_START` | `1` | Sondear al arrancar |
| `DASHBOARD_NITTER_INSTANCES` | 2 instancias | Respaldo, separadas por comas |
| `DASHBOARD_RETENTION_HOURS` | `48` | Horas que dura una publicación sin marcar |
| `TELEGRAM_BOT_TOKEN` | vacío | Credencial del bot para la futura entrega |
| `TELEGRAM_CHAT_ID` | vacío | Chat de destino autorizado para la futura entrega |

Las variables de análisis e IA ya no participan en el dashboard. Las variables
de Telegram solo muestran si existe configuración; la entrega real se habilita
cuando quede fijado el contrato del bot receptor.

## Datos locales

Todo queda en `dashboard/var/`, ignorado por Git:

```
dashboard/var/
  dashboard.sqlite3   estado (cuentas y publicaciones)
  profiles/x/         perfil de Chrome para leer X
  media/              [heredado] medios que descargaba el compositor
  cards/              [heredado] tarjetas del flujo anterior
```

`media/` y `cards/` son restos de cuando el dashboard componía tarjetas. Se
pueden borrar sin más: la bandeja usa las miniaturas del CDN de X, no archivos
locales.

---

## Pruebas

```powershell
python -m unittest discover -s tests -v      # suite completa (201 tests)
python -m unittest tests.test_dashboard_core tests.test_dashboard_integration
```

Los tests del dashboard no salen a la red: usan fixtures de RSS, una base de
datos temporal y un servidor propio en un puerto libre. Los tests del
compositor (`test_compose_image.py`) son del repositorio y siguen pasando.

---

## Abrirlo desde el móvil (misma red)

Por defecto escucha solo en `127.0.0.1`, así que **ningún otro dispositivo
puede entrar**. Para abrirlo desde el teléfono:

```powershell
python -m dashboard --lan
```

Al escuchar en la red local, el dashboard **genera una clave de acceso** y la
imprime con la URL completa, por ejemplo:

```
http://10.0.0.44:8765/?token=hMYkGn-kyL0XjI3d
```

Ábrela una vez en el móvil: la clave queda en una cookie y ya no hace falta
repetirla. Se guarda en la base de datos, así que no cambia entre reinicios.
Si quieres fijar la tuya, ponla en `DASHBOARD_ACCESS_TOKEN`.

> **Firewall.** Si el Ethernet está en perfil «Público» (lo habitual), Windows
> bloquea la entrada y el móvil no cargará la página. Hay que permitir el
> puerto **una vez**, en un PowerShell **como administrador**:
>
> ```powershell
> New-NetFirewallRule -DisplayName "EditImg Dashboard 8765" `
>   -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Any
> ```
>
> Alternativa sin tocar el firewall: si tienes Tailscale en el móvil, entra por
> la IP de Tailscale del PC (aparece también en la lista de URLs al arrancar).

---

## Hora y zona horaria

Dos problemas distintos, resueltos por separado:

1. **El reloj del sistema puede estar mal.** Se ha medido un desfase real de
   seis horas en esta máquina. El dashboard pide la cabecera `Date` a varios
   servidores públicos (Google, Cloudflare, Bing, GitHub), toma la **mediana** y
   ancla ahí la hora usando el reloj monotónico. A partir de ese momento la hora
   **no depende del reloj del PC**: aunque alguien lo cambie, el dashboard sigue
   bien.
2. **La zona configurada puede no ser la del usuario.** Windows estaba en zona
   europea mientras el usuario está en Quebec. El huso se resuelve con reglas
   (`America/Toronto`, con horario de verano), no con lo que diga el sistema.

La comprobación de consenso usa la mediana en lugar del máximo menos el mínimo:
un servidor con la hora algo desviada (la de GitHub iba 6 s por detrás) no
invalida una medición correcta. La pestaña **Estado y ajustes** muestra el
desfase detectado y de dónde sale el huso.

---

## Flujo de trabajo en la interfaz

Tres pestañas: **Bandeja**, **Cuentas** y **Ajustes**.

**Bandeja.** Lista **cronológica de una columna**, de más reciente a más antigua,
agrupada por día (`Hoy`, `Ayer`, `30 sep 2026`). Cada publicación muestra la
cuenta, el autor real (los reposts se atribuyen bien), el texto, las miniaturas
—con una etiqueta **vídeo** cuando lo son— y la **hora relativa** (`hace 2 h
15 min`) con la fecha exacta al lado.

Tres botones por publicación, y ninguno más:

| Botón | Qué hace |
|---|---|
| **Ver original** | Abre la publicación en X, en otra pestaña |
| **Copiar enlace** | Copia su URL al portapapeles |
| **Marcar listo** | La aparta: **no se borra** en la limpieza. Se puede desmarcar |

El filtro **Ver** cambia entre *Pendientes*, *Marcadas como listas* y *Todas*.
Al marcar una publicación como lista desaparece de *Pendientes* y pasa a
*Marcadas como listas*, que es donde se puede desmarcar.

Debajo está el filtro de tipo: **Todas**, **Imágenes** y **Vídeos**. Es un filtro
real del servidor, no solo visual; una publicación con vídeo queda en **Vídeos**
aunque X no entregue una miniatura. Las tarjetas muestran además el tipo
detectado y, si falta la portada, explican que hay que abrirla en X para verla.
Si una publicación trae imagen y vídeo, entra en **Vídeos**; dentro de la
tarjeta cada miniatura conserva su propio tipo, para que una imagen acompañante
no se confunda con el vídeo.

**Cuentas.** Añadir, pausar y quitar cuentas de X.

**Ajustes.** Estado de la limpieza —con un botón para forzarla—, las fuentes de
publicaciones, los ajustes efectivos y los últimos movimientos.

### Copiar enlace, que tiene truco

`navigator.clipboard` **solo funciona en contexto seguro** (HTTPS o localhost).
Al entrar desde el móvil por `http://<ip-de-tailscale>:8765` no existe, así que
el botón usa un respaldo con un campo temporal; y si el navegador tampoco lo
permite, enseña el enlace para copiarlo a mano. Sin eso, en el móvil no habría
hecho nada.

---

## No repetir publicaciones y limpieza a las 48 horas

Aquí hay una trampa que conviene entender: **borrar una publicación de la
bandeja no impide que vuelva**. La deduplicación se apoya justo en ese
registro, así que si se borrara sin más, la siguiente búsqueda la insertaría
otra vez como nueva y se repetiría *más*, no menos.

Por eso hay dos capas separadas:

| Capa | Qué guarda | Se borra |
|---|---|---|
| `seen_tweets` | Identificador, fecha, autor y huella. Unos 60 bytes por fila | **Nunca** |
| `tweets` + archivos | Texto, medios y estado, lo que se ve | A las 48 h, **salvo lo marcado como listo** |

Resultado: la bandeja se mantiene corta y ligera, y nada vuelve a aparecer.

**La excepción es «listo».** La limpieza borra por edad sin mirar nada más, así
que una publicación marcada como lista **no se toca nunca**, haga la edad que
haga. Es la razón de existir de ese botón: sin él, la retención se llevaría
justo lo que se quería conservar. Al desmarcarla vuelve al montón.

**Dos barreras contra las repeticiones:**

1. **Identificador.** Si un post ya se vio alguna vez, no entra de nuevo — ni
   siquiera después de haberse limpiado.
2. **Contenido.** Se calcula una huella del texto (sin enlaces ni hashtags) más
   el primer medio y el autor. Si otra publicación de la misma cuenta coincide
   dentro de la ventana configurada, se guarda marcada como `duplicado` en
   lugar de colarse como nueva.

La huella exige un mínimo de texto cuando no hay imágenes: así dos
publicaciones cortas iguales («GG», «🚨», «NUEVO») no se confunden entre sí.
Y como incluye el autor, dos cuentas distintas contando lo mismo no se pisan.

La limpieza corre sola después de cada sondeo (como mucho una vez por hora) y
también a mano con **Limpiar ahora**. El estado se ve en **Ajustes**.

---

## En el móvil

La interfaz está adaptada para usarla desde el teléfono:

- Una sola columna, con los filtros a ancho completo.
- Campos de **16 px**, que es lo que evita que iOS haga zoom al enfocarlos y
  descuadre la página.
- Objetivos táctiles de 44 px, y **los tres botones de cada publicación a lo
  ancho**: se pulsan con el pulgar sin apuntar.
- Cabecera no fija y pestañas deslizables, para no comerse la pantalla.
- Márgenes de zona segura (`env(safe-area-inset-*)`) para los móviles con
  notch, y toasts a lo ancho.
- La **primera publicación se ve sin bajar**: los filtros y la ayuda plegada
  ocupan lo justo.

Se entra con `python -m dashboard --lan` y abriendo desde el móvil la dirección
con `?token=` que imprime al arrancar.

---

## Dejarlo funcionando (y accesible desde fuera)

Mientras el dashboard se arranca a mano, vive dentro de la sesión que lo lanzó:
si esa ventana se cierra, el servidor muere. Eso deja sin acceso justo cuando
más importa, que es estando fuera de casa.

`dashboard/start-dashboard.cmd` es el lanzador: comprueba que el puerto esté
libre, arranca el dashboard y va escribiendo en `dashboard/var/dashboard.log`.
Se puede ejecutar con doble clic, y es lo que usa la tarea programada.

La tarea **EditImg Dashboard** arranca el lanzador al iniciar sesión y lo
reinicia hasta tres veces si falla. Se registra sin permisos de administrador:

```powershell
$cmd = "$PWD\dashboard\start-dashboard.cmd"
Register-ScheduledTask -TaskName 'EditImg Dashboard' `
  -Action (New-ScheduledTaskAction -Execute $cmd) `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME") `
  -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
      -DontStopIfGoingOnBatteries -RestartCount 3 `
      -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)) `
  -Principal (New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
      -LogonType Interactive -RunLevel Limited) -Force
```

Comprobar el estado y los últimos resultados:

```powershell
Get-ScheduledTask -TaskName 'EditImg Dashboard' | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName 'EditImg Dashboard'
Get-Content dashboard\var\dashboard.log -Tail 30
```

**Dos cosas que hay que tener presentes para el acceso remoto:**

- La tarea arranca al **iniciar sesión**. Si cierras la sesión de Windows (no
  basta con bloquear la pantalla), el dashboard se detiene. **Bloquea, no
  cierres sesión.** Para que sobreviva a un cierre de sesión haría falta
  registrarla como «ejecutar tanto si el usuario ha iniciado sesión como si
  no», y eso pide permisos de administrador.
- El PC tiene que seguir encendido. En este equipo la suspensión con corriente
  alterna está en **nunca**, que es lo correcto para esto.

---

## Límites conocidos

- **Los vídeos se ven como miniatura**, no se reproducen dentro del dashboard.
  X publica una imagen de portada y un enlace; el vídeo se ve abriendo la
  publicación. Si X no entrega portada, el radar conserva la señal de vídeo y
  deja la tarjeta lista para abrirla. Incrustar el reproductor obligaría a
  depender del marcado de X.
- **Copiar enlace depende del navegador.** En contexto seguro (HTTPS o
  localhost) usa el portapapeles moderno; por HTTP sin cifrar —el caso del móvil
  por Tailscale— recurre a un respaldo, y si el navegador lo bloquea enseña el
  enlace para copiarlo a mano.
- **Fecha de publicación.** X ya no expone la fecha exacta en su web (solo
  «hace 9 h»), así que se resuelve consultando la misma API que ya usaba el
  repositorio al enriquecer cada publicación nueva.
- **Reposts.** Una timeline puede incluir publicaciones de otras cuentas. Se
  guardan `origen` y `autor` por separado para no atribuirlas mal.
- **Nitter** son instancias de terceros: caen a menudo. De ahí la rotación
  automática y el respaldo por navegador.
- **Estados heredados.** Las publicaciones que se convirtieron en tarjeta con el
  flujo anterior siguen en la base con su estado (`tarjeta_lista`, `enviado`).
  No estorban —cuentan como atendidas— y se pueden marcar como listas o
  descartar como cualquier otra.
