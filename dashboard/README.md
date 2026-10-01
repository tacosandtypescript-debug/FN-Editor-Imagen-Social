# EditImg Dashboard

Capa de organización **por encima** del flujo que ya existe en el repositorio.
No lo sustituye ni lo modifica: lo orquesta y lo automatiza.

```
Cuentas de X → búsqueda automática → publicaciones nuevas → dashboard
   → selección manual → análisis con IA → generación de tarjeta
   → edición/revisión → envío a Telegram
```

Nada se convierte en tarjeta automáticamente. El sistema trae las
publicaciones y **tú eliges** cuáles se convierten.

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
| Composición | `bin/compose_image.py` | Se invoca como proceso, igual que hoy |
| Descarga de medios | `bin/fetch_media.py` | Orden, límite de 25 MB y validación originales |
| Comando del compositor | `edit_link.build_composer_command` | Se reutiliza tal cual |
| Preset automático | `edit_link.select_auto_preset` | Elige 9:16 / 1:1 / 16:9 por orientación |
| Validación de salida | `edit_link.validate_rendered_output` | Verifica formato, modo y dimensiones |
| Límites | `bin/runtime_config.py` | `DEFAULT_MAX_IMAGES` y compañía |
| Reglas editoriales | `.hermes.md` y las `SKILL.md` | Codificadas en el prompt y en el saneado |
| Paleta y marca | `bin/preset*.json` | Leídas del preset real, no duplicadas |

**No se ha modificado** ningún archivo existente de `bin/`, los presets, las
fuentes, las skills ni los tests originales. Los 51 tests previos siguen
pasando.

---

## Módulos

```
dashboard/
  config.py              ajustes, rutas y lectura de .env
  store.py               SQLite: cuentas, publicaciones, tarjetas, entregas
  service.py             lógica del flujo (sin HTTP, testeable)
  server.py              servidor HTTP y API JSON (biblioteca estándar)
  poller.py              sondeo automático en segundo plano
  pipeline/
    repo.py              carga bin/*.py sin modificarlos
    media.py             descarga y sondeo de medios
    cards.py             composición y validación
  providers/
    base.py              interfaces comunes
    timelines.py         descubrimiento: navegador / Nitter / X API
    analysis.py          análisis: OpenAI-compatible / ChatGPT / manual
    telegram.py          entrega: Bot API (sendDocument) / archivo local
  web/                   interfaz (HTML + CSS + JS sin dependencias)
```

Cada capacidad vive detrás de una interfaz propia: cambiar el proveedor de
análisis no toca el de descubrimiento ni el de entrega.

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

### Análisis

| Proveedor | Requiere | Estado |
|---|---|---|
| `openai` | clave de API **o** un modelo local | Por defecto y recomendado |
| `chatgpt` | Playwright + sesión iniciada en chatgpt.com | Experimental |
| `manual` | nada | Redactas tú; siempre funciona |

El prompt del sistema aplica las reglas del repositorio: titular de 3 a 12
palabras, color solo en palabras con carga semántica, como máximo dos acentos y
cinco hashtags únicos incluyendo `#khetzalgg`.

### Entrega

| Proveedor | Requiere |
|---|---|
| `telegram` | `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` |
| `local` | nada; deja el archivo en `dashboard/var/cards/` |

La entrega usa **`sendDocument`**, nunca `sendPhoto`, tal y como exige la regla
del repositorio: el PNG viaja sin comprimir.

---

## Sobre la suscripción de ChatGPT

Es la pregunta importante y conviene ser claro.

**Una suscripción de ChatGPT (Plus, Pro…) no incluye acceso a la API.** Son
productos y facturaciones distintas. No existe una forma oficial de «usar tu
suscripción» desde un programa.

Lo que sí se puede hacer, y está implementado:

1. **Proveedor `chatgpt` (experimental).** Abre `chatgpt.com` en Chrome con un
   perfil propio, escribe el prompt y lee la respuesta. **Esto sí consume tu
   suscripción** y no cuesta nada por uso. A cambio:
   - depende del marcado de una web ajena: cuando OpenAI lo cambie, dejará de
     funcionar hasta ajustarlo;
   - es más lento que una API;
   - la automatización de la interfaz puede toparse con límites de uso.

   Para activarlo:

   ```powershell
   pip install -r requirements-dashboard.txt
   python -m dashboard
   ```

   Después abre el perfil `dashboard/var/profiles/chatgpt` con Chrome, inicia
   sesión **una vez**, y elige el proveedor `chatgpt` en el editor. Si no hay
   sesión, el dashboard lo detecta y te lo dice con un mensaje claro.

2. **Proveedor `openai` con un modelo local (la opción estable y gratis).**
   Cualquier servidor compatible con OpenAI sirve. Con Ollama:

   ```
   DASHBOARD_ANALYSIS_PROVIDER=openai
   DASHBOARD_OPENAI_BASE_URL=http://localhost:11434/v1
   DASHBOARD_OPENAI_MODEL=llama3.1
   OPENAI_API_KEY=local
   ```

   Sin coste por uso, sin depender de terceros y sin que se rompa cuando cambie
   una web. Para este caso de uso (un titular corto y un caption) un modelo
   local pequeño va sobrado.

3. **Proveedor `openai` con la API oficial**, si algún día prefieres pagar por
   uso a cambio de la máxima estabilidad.

**Recomendación:** usa `manual` o un modelo local como forma habitual, y el
proveedor `chatgpt` cuando quieras aprovechar la suscripción asumiendo que es
una pieza que puede requerir mantenimiento.

---

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
| `DASHBOARD_ANALYSIS_PROVIDER` | `openai` | `openai`, `chatgpt` o `manual` |
| `TELEGRAM_BOT_TOKEN` | vacío | Token del bot de @BotFather |
| `TELEGRAM_CHAT_ID` | vacío | Tu chat con el bot |
| `DASHBOARD_DEFAULT_RESOLUTION` | `4k` | `4k` o `native` |

### Telegram en 3 pasos

1. Habla con **@BotFather** en Telegram y crea un bot: te dará el token.
2. Escríbele algo a tu bot (si no, no puede iniciar la conversación).
3. Averigua tu `chat_id` (por ejemplo con **@userinfobot**) y ponlo en
   `dashboard/.env`.

El estado de la integración aparece en la pestaña **Estado y ajustes**; si
falta algo, el dashboard te dice exactamente qué.

---

## Datos locales

Todo queda en `dashboard/var/`, ignorado por Git:

```
dashboard/var/
  dashboard.sqlite3      estado (cuentas, publicaciones, tarjetas, entregas)
  media/<tweet_id>/      medios descargados (se reutilizan al recomponer)
  cards/<tweet_id>-v1.png tarjetas generadas
  profiles/x/            perfil de Chrome para leer X
  profiles/chatgpt/      perfil de Chrome para ChatGPT
```

Los medios se descargan **una sola vez**: al editar y recomponer no se vuelve a
salir a la red, así que el re-render es rápido.

---

## Pruebas

```powershell
python -m unittest discover -s tests -v      # suite completa (99 tests)
python -m unittest tests.test_dashboard_core tests.test_dashboard_integration
```

Los tests del dashboard no salen a la red: usan fixtures de RSS, un compositor
real con imágenes generadas al vuelo y un servidor OpenAI simulado.

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

1. **Bandeja.** Lista **cronológica de una sola columna**, de más reciente a más
   antigua, agrupada por día (`Hoy`, `Ayer`, `30 sep 2026`). Cada publicación
   muestra cuenta, autor real (los reposts se atribuyen bien), texto,
   miniaturas, **hora relativa** (`hace 2 h 15 min`) con la fecha exacta al lado,
   enlace original y estado.
2. **Procesar.** Un botón por publicación que hace el trabajo completo:
   análisis del texto, descarga de medios y composición de la tarjeta.
3. **Abrir editor.** Al terminar aparece al lado el botón que abre el **editor
   independiente** en su propia pestaña.
4. **Editor independiente** (`/editor.html?card=N`). Previsualización grande,
   edición de texto y composición, entrega e historial. Lo único que se puede
   **regenerar con IA** es el texto —titular de arriba, texto de abajo y
   caption—: no toca las imágenes ni ningún otro ajuste, y recompone la tarjeta
   sola. Admite una indicación opcional («más corto», «otro enfoque») y recibe
   la versión anterior para no repetirla.

---

## Límites conocidos

- **Publicaciones sin imágenes.** El compositor canónico necesita al menos una
  imagen, así que un tweet de solo texto no se puede convertir en tarjeta. El
  dashboard las marca en la bandeja en lugar de fallar en silencio.
- **Fecha de publicación.** X ya no expone la fecha exacta en su web (solo
  «hace 9 h»), así que se resuelve consultando la misma API que ya usaba el
  repositorio al enriquecer cada publicación nueva.
- **Reposts.** Una timeline puede incluir publicaciones de otras cuentas. Se
  guardan `origen` y `autor` por separado para no atribuirlas mal.
- **Nitter** son instancias de terceros: caen a menudo. De ahí la rotación
  automática y el respaldo por navegador.
- **El proveedor `chatgpt`** es experimental y puede requerir ajustes cuando
  OpenAI cambie su web.
