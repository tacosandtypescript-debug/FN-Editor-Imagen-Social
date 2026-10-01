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
python -m dashboard --port 9000      # otro puerto
python -m dashboard --no-poller      # sin sondeo automático
python -m dashboard --host 0.0.0.0   # exponerlo en la red local (ojo)
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
