/* EditImg Dashboard — interfaz en JavaScript puro, sin dependencias. */
"use strict";

const $ = (id) => document.getElementById(id);

/**
 * Iconos en SVG, no emoji.
 *
 * El emoji se ve distinto en cada sistema, no hereda el color del texto y los
 * lectores de pantalla lo leen en voz alta. Con SVG se controla el trazo, el
 * tamaño y la accesibilidad.
 */
const ICON = {
  reloj:
    `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" ` +
    `stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false">` +
    `<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/></svg>`,
  visto:
    `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ` +
    `stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ` +
    `focusable="false"><path d="M20 6L9 17l-5-5"/></svg>`,
};

/** Indicador de carga accesible, en lugar de un emoji girando. */
function spinner(label) {
  return `<span class="spinner" role="status" aria-label="${escapeHtml(label)}"></span>`;
}

const app = {
  state: null,
  tweets: [],
  cards: [],
  selectedId: null,
  card: null,
  busy: false,
  //: Día actual en la hora local del usuario, según el reloj verificado.
  today: null,
  //: Hora verificada del servidor y lectura del reloj del navegador en ese
  //: momento. Con las dos se puede avanzar la hora sin volver a preguntar.
  verifiedNowMs: null,
  clientAtMs: null,
};

/* ------------------------------------------------------------------ */
/* Utilidades                                                          */
/* ------------------------------------------------------------------ */
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }
  if (!response.ok) {
    const message = (payload && payload.error) || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function toast(message, kind = "ok") {
  const box = $("toast");
  const node = document.createElement("div");
  node.className = kind;
  node.textContent = message;
  box.appendChild(node);
  setTimeout(() => node.remove(), kind === "error" ? 9000 : 4200);
}

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function formatDate(value) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("es-ES", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

/**
 * Hora actual según el reloj **verificado** del servidor, avanzada con el
 * reloj del navegador desde la última consulta.
 *
 * Es importante no usar `Date.now()` a secas: el reloj de esta máquina iba
 * seis horas desviado y las horas habrían salido mal.
 */
function verifiedNowMs() {
  if (app.verifiedNowMs === null) return Date.now();
  return app.verifiedNowMs + (Date.now() - app.clientAtMs);
}

function rememberServerClock(clock) {
  if (!clock || !clock.now_utc) return;
  const parsed = new Date(clock.now_utc);
  if (Number.isNaN(parsed.getTime())) return;
  app.verifiedNowMs = parsed.getTime();
  app.clientAtMs = Date.now();
}

/** Mismas reglas que el servidor: «hace 2 h 15 min», «hace 3 días»… */
function humanizeSince(isoDate, nowMs) {
  if (!isoDate) return "";
  const then = new Date(isoDate).getTime();
  if (Number.isNaN(then)) return "";
  let seconds = Math.floor(((nowMs === undefined ? verifiedNowMs() : nowMs) - then) / 1000);
  if (seconds < 0) seconds = 0;              // reloj desviado: nunca «en el futuro»
  if (seconds < 60) return "hace unos segundos";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  if (hours < 24) return restMinutes ? `hace ${hours} h ${restMinutes} min` : `hace ${hours} h`;
  const days = Math.floor(seconds / 86400);
  if (days === 1) return "hace 1 día";
  if (days < 30) return `hace ${days} días`;
  const months = Math.floor(days / 30);
  if (months === 1) return "hace 1 mes";
  if (months < 12) return `hace ${months} meses`;
  const years = Math.floor(days / 365);
  return years <= 1 ? "hace 1 año" : `hace ${years} años`;
}

/**
 * Vuelve a pintar las horas relativas sin pedir nada al servidor.
 *
 * Antes se quedaban congeladas: el refresco periódico actualizaba contadores y
 * bitácora, pero no la lista, así que una publicación se quedaba en «hace 17
 * minutos» indefinidamente.
 */
function tickRelativeTimes() {
  const nowMs = verifiedNowMs();
  for (const node of document.querySelectorAll("[data-posted]")) {
    const iso = node.getAttribute("data-posted");
    if (!iso) continue;
    const label = humanizeSince(iso, nowMs);
    if (label && node.textContent !== label) node.textContent = label;
  }
}

function withBusy(button, label, task) {
  const original = button ? button.textContent : null;
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.innerHTML = `${spinner(label)} ${escapeHtml(label)}`;
  }
  return Promise.resolve()
    .then(task)
    .finally(() => {
      if (button) {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = original;
      }
    });
}

/* ------------------------------------------------------------------ */
/* Estado general                                                      */
/* ------------------------------------------------------------------ */
async function loadState() {
  const state = await api("/api/state");
  app.state = state;
  // «Hoy» y «ayer» se calculan con la hora verificada, no con la del navegador:
  // el reloj de la máquina puede estar desviado.
  app.today = ((state.clock && state.clock.now_local) || "").slice(0, 10) || null;
  rememberServerClock(state.clock);
  renderProviderPills(state);
  renderCounts(state.counts);
  renderAccounts(state.accounts);
  renderProviders(state);
  renderSettings(state.settings);
  renderAnalysisStatus(state);
  renderMaintenance(state);
  renderPollerState(state);
  renderEvents(state.events);
  renderAnalysisOptions(state.analysis_providers);
  renderStyleOptions();
  fillAccountFilter(state.accounts);
}

function renderProviderPills(state) {
  const groups = [
    ["Descubrir", state.timeline_providers],
    ["Análisis", state.analysis_providers],
    ["Entrega", state.delivery_providers],
  ];
  const parts = [];
  for (const [label, list] of groups) {
    for (const provider of list || []) {
      const cls = provider.available ? "ok" : "off";
      parts.push(
        `<span class="pill ${cls}" title="${escapeHtml(provider.detail || "")}">` +
        `<span class="dot"></span>${escapeHtml(label)}: ${escapeHtml(provider.name)}</span>`
      );
    }
  }
  const clock = state.clock;
  if (clock) {
    // El reloj del sistema puede estar desviado: se avisa en lugar de mostrar
    // horas mal sin explicación.
    const skewed = Math.abs(clock.offset_seconds || 0) > 60;
    parts.push(
      `<span class="pill ${skewed ? "warn" : "ok"}" title="${escapeHtml(
        skewed
          ? "Las horas se calculan contra servidores públicos de hora, no contra el reloj del sistema."
          : "El reloj del sistema coincide con la hora de referencia."
      )}">` +
      `<span class="dot"></span>${ICON.reloj} ${escapeHtml(clock.local_offset_human || "UTC")}` +
      (skewed ? ` · reloj ${escapeHtml(clock.offset_human || "")}` : "") +
      `</span>`
    );
  }
  $("provider-pills").innerHTML = parts.join("");
}

function renderCounts(counts) {
  if (!counts) return;
  $("counts").innerHTML = Object.entries(counts)
    .map(([key, value]) => `<span class="pill">${escapeHtml(key)}: ${value}</span>`)
    .join("");
}

function renderEvents(events) {
  const list = events || [];
  $("events").innerHTML = list.length
    ? list.map((event) => (
        `<div class="${event.level === "error" ? "error" : ""}">` +
        `<span class="muted">${escapeHtml(formatDate(event.ts))}</span> ` +
        `${escapeHtml(event.message)}</div>`
      )).join("")
    : `<div class="muted">Sin actividad todavía.</div>`;
}

function renderProviders(state) {
  const rows = [];
  const push = (title, list) => {
    rows.push(`<p class="small" style="margin:10px 0 4px"><strong>${title}</strong></p>`);
    for (const provider of list || []) {
      rows.push(
        `<div class="row" style="margin-bottom:6px">` +
        `<span class="pill ${provider.available ? "ok" : "off"}">${escapeHtml(provider.name)}</span>` +
        `<span class="small muted">${escapeHtml(provider.detail || "")}</span></div>`
      );
    }
  };
  push("Descubrimiento de publicaciones", state.timeline_providers);
  push("Análisis editorial", state.analysis_providers);
  push("Entrega", state.delivery_providers);
  if (state.profiles) {
    rows.push(
      `<p class="small muted" style="margin-top:12px">Perfiles de navegador: ` +
      `<code>${escapeHtml(state.profiles.x || "")}</code> · ` +
      `<code>${escapeHtml(state.profiles.chatgpt || "")}</code></p>`
    );
  }
  $("providers").innerHTML = rows.join("");
}

function renderAnalysisStatus(state) {
  const box = $("chatgpt-state");
  if (!box) return;
  const info = state.analysis_status || {};
  const configured = (state.settings && state.settings.analysis_provider) || "";
  const codex = (info.providers || []).find((provider) => provider.name === "codex") || {};
  const logged = info.chatgpt_logged_in;

  const codexBadge = codex.available
    ? `<span class="pill ok"><span class="dot"></span>CLI de Codex listo</span>`
    : `<span class="pill warn"><span class="dot"></span>CLI de Codex no disponible</span>`;
  const authBadge = info.codex_auth_mode === "chatgpt"
    ? `<span class="pill ok">suscripción de ChatGPT</span>`
    : info.codex_auth_mode
      ? `<span class="pill warn">modo ${escapeHtml(info.codex_auth_mode)}</span>`
      : `<span class="pill off">sin sesión</span>`;
  const webBadge = logged === true
    ? `<span class="pill ok">navegador: sesión iniciada</span>`
    : `<span class="pill off">navegador: sin sesión</span>`;

  box.innerHTML =
    `<span class="pill ${configured === "codex" ? "ok" : ""}">proveedor activo: ${escapeHtml(configured || "—")}</span>` +
    codexBadge + authBadge + webBadge +
    `<span class="small muted">${escapeHtml(codex.detail || "")}</span>` +
    (info.codex_executable
      ? `<span class="small muted">ejecutable: <code>${escapeHtml(info.codex_executable)}</code></span>`
      : "");
}

async function openChatgptLogin() {
  const button = $("btn-chatgpt-login");
  await withBusy(button, "Abriendo…", async () => {
    try {
      const result = await api("/api/profiles/chatgpt/open", { method: "POST", body: "{}" });
      toast("Ventana de ChatGPT abierta. Inicia sesión ahí y vuelve a esta página.");
      $("analysis-test-result").innerHTML =
        `<span class="pill ok">ventana abierta</span> Inicia sesión en la ventana de Chrome ` +
        `y después pulsa «Probar análisis». Perfil: <code>${escapeHtml(result.profile || "")}</code>`;
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function testAnalysis() {
  const button = $("btn-analysis-test");
  // Se usa la publicación más reciente que tenga imágenes.
  const candidate = (app.tweets || []).find((tweet) => (tweet.media || []).length);
  if (!candidate) {
    return toast("No hay ninguna publicación con imágenes para probar.", "error");
  }
  await withBusy(button, "Probando…", async () => {
    try {
      const payload = await api("/api/analysis/test", {
        method: "POST",
        body: JSON.stringify({
          tweet_id: candidate.tweet_id,
          provider: ($("f-analysis") && $("f-analysis").value) || null,
        }),
      });
      const analysis = payload.analysis || {};
      $("analysis-test-result").innerHTML =
        `<span class="pill ok">${escapeHtml(analysis.provider || "")}</span> ` +
        `<strong>titular:</strong> ${escapeHtml(analysis.top || "")}<br>` +
        `<strong>contexto:</strong> ${escapeHtml(analysis.bottom || "")}<br>` +
        `<strong>hashtags:</strong> ${escapeHtml((analysis.hashtags || []).join(" "))}<br>` +
        `<span class="muted">${escapeHtml(analysis.reasoning || "")}</span>`;
      toast("El análisis funciona.");
    } catch (error) {
      $("analysis-test-result").innerHTML =
        `<span class="pill off">error</span> ${escapeHtml(error.message)}`;
      toast(error.message, "error");
    }
  });
}

function renderMaintenance(state) {
  const box = $("maintenance-state");
  if (!box) return;
  const info = state.maintenance || {};
  const hours = info.retention_hours;
  const due = info.purge_due
    ? `<span class="pill warn">toca limpiar</span>`
    : `<span class="pill ok">al día</span>`;
  box.innerHTML =
    `<span class="pill">retención: ${escapeHtml(String(hours))} h</span>` +
    `<span class="pill">duplicados: ${escapeHtml(String(info.duplicate_window_days))} días</span>` +
    `<span class="pill ok">${escapeHtml(String(info.total_seen || 0))} vistas recordadas</span>` +
    due +
    `<span class="small muted">última limpieza: ${escapeHtml(info.last_purge_at ? formatDate(info.last_purge_at) : "nunca")}</span>`;
}

async function purge() {
  const button = $("btn-purge");
  await withBusy(button, "Limpiando…", async () => {
    try {
      const result = await api("/api/maintenance/purge", {
        method: "POST",
        body: JSON.stringify({ force: true }),
      });
      $("purge-result").innerHTML =
        `<span class="pill ok">hecho</span> ${escapeHtml(String(result.purged || 0))} publicación(es) ` +
        `y ${escapeHtml(String(result.files_removed || 0))} archivo(s) borrados · ` +
        `se recuerdan ${escapeHtml(String(result.seen_kept || 0))} para no repetirlas`;
      toast("Limpieza completada.");
      await loadTweets();
      await loadState();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** Estado del sondeo automático: deja claro si busca solo y cuándo toca. */
function renderPollerState(state) {
  const box = $("poller-state");
  if (!box) return;
  const poller = state.poller || {};
  const parts = [];

  if (!poller.periodic) {
    parts.push(
      `<span class="pill warn"><span class="dot"></span>búsqueda automática desactivada</span>`,
      `<span class="muted">Arranca con <code>python -m dashboard --lan</code> (sin <code>--no-poller</code>) ` +
      `para que busque sola. El botón «Buscar ahora» funciona igual.</span>`
    );
  } else if (poller.busy) {
    const progress = poller.progress || {};
    parts.push(
      `<span class="pill ok">${spinner("buscando")}buscando… ${progress.done || 0}/${progress.total || "?"}</span>`,
      progress.current ? `<span class="muted">leyendo @${escapeHtml(progress.current)}</span>` : ""
    );
  } else {
    parts.push(`<span class="pill ok"><span class="dot"></span>búsqueda automática activa</span>`);
    parts.push(`<span class="muted">cada ${escapeHtml(formatDuration(poller.interval_seconds))}</span>`);
    if (poller.next_run_in_seconds !== null && poller.next_run_in_seconds !== undefined) {
      parts.push(`<span class="muted">· próxima en ${escapeHtml(formatDuration(poller.next_run_in_seconds))}</span>`);
    }
    if (poller.last_run_at) {
      parts.push(`<span class="muted">· última: ${escapeHtml(formatDate(poller.last_run_at))}</span>`);
    } else {
      parts.push(`<span class="muted">· todavía no ha buscado en esta sesión</span>`);
    }
  }
  if (poller.last_error) {
    parts.push(`<span class="pill off">último error: ${escapeHtml(poller.last_error)}</span>`);
  }

  // Trabajos de procesado: se ven aunque el móvil haya perdido la conexión.
  const jobs = state.jobs || {};
  if (jobs.busy) {
    parts.push(
      `<span class="pill warn">${spinner("procesando")}procesando` +
      (jobs.pending ? ` (+${jobs.pending} en cola)` : "") + `</span>`
    );
  }
  box.innerHTML = parts.join(" ");
}

function renderSettings(settings) {
  if (!settings) return;
  const interesting = [
    ["Proveedor de descubrimiento", settings.timeline_provider],
    ["Intervalo de sondeo", `${settings.poll_interval_seconds} s`],
    ["Sondeo al arrancar", settings.poll_on_start ? "sí" : "no"],
    ["Instancias Nitter", (settings.nitter_instances || []).join(", ")],
    ["Navegador", `${settings.browser_channel} · headless: ${settings.browser_headless}`],
    ["Proveedor de análisis", settings.analysis_provider],
    ["Endpoint de análisis", settings.openai_base_url],
    ["Modelo", settings.openai_model],
    ["Clave de API", settings.openai_api_key_set ? "configurada" : "no configurada"],
    ["Token de X API", settings.x_api_bearer_set ? "configurado" : "no configurado"],
    ["Telegram", settings.telegram_configured ? "configurado" : "no configurado"],
    ["Formato por defecto", settings.default_format],
    ["Resolución por defecto", settings.default_resolution],
  ];
  $("settings").innerHTML = interesting
    .map(([key, value]) => (
      `<div class="row" style="justify-content:space-between;border-bottom:1px solid #241b40;padding:5px 0">` +
      `<span class="small muted">${escapeHtml(key)}</span>` +
      `<span class="small">${escapeHtml(value)}</span></div>`
    )).join("");
}

function renderAnalysisOptions(providers) {
  const select = $("f-analysis");
  if (!select || select.dataset.filled === "1") return;
  select.innerHTML = (providers || [])
    .map((provider) => (
      `<option value="${escapeHtml(provider.name)}" ${provider.available ? "" : "disabled"}>` +
      `${escapeHtml(provider.name)}${provider.available ? "" : " (no disponible)"}</option>`
    )).join("");
  const preferred = app.state && app.state.settings && app.state.settings.analysis_provider;
  if (preferred) select.value = preferred;
  select.dataset.filled = "1";
}

function renderStyleOptions() {
  const select = $("f-style");
  if (select.dataset.filled === "1") return;
  const styles = ["auto", "adaptive", "grid", "bento", "mosaico", "puzzle", "jerarquico", "asimetrico"];
  select.innerHTML = styles.map((style) => `<option value="${style}">${style}</option>`).join("");
  select.dataset.filled = "1";
}

function fillAccountFilter(accounts) {
  const select = $("filter-account");
  const previous = select.value;
  select.innerHTML = `<option value="">Todas</option>` + (accounts || [])
    .map((account) => `<option value="${escapeHtml(account.handle)}">@${escapeHtml(account.handle)}</option>`)
    .join("");
  select.value = previous;
}

/* ------------------------------------------------------------------ */
/* Cuentas                                                             */
/* ------------------------------------------------------------------ */
function renderAccounts(accounts) {
  const list = accounts || [];
  if (!list.length) {
    $("accounts").innerHTML = `<div class="empty">Todavía no hay cuentas. Añade una arriba.</div>`;
    return;
  }
  $("accounts").innerHTML = list.map((account) => `
    <div class="row" style="border-bottom:1px solid #241b40;padding:8px 0;justify-content:space-between">
      <div>
        <strong>@${escapeHtml(account.handle)}</strong>
        <div class="small muted">
          ${account.active ? "activa" : "pausada"} ·
          última revisión: ${account.last_checked_at ? escapeHtml(formatDate(account.last_checked_at)) : "nunca"}
          ${account.last_error ? ` · <span style="color:#ff9db0">${escapeHtml(account.last_error)}</span>` : ""}
        </div>
      </div>
      <div class="row">
        <button class="small ghost" data-toggle="${escapeHtml(account.handle)}" data-active="${account.active ? "1" : "0"}">
          ${account.active ? "Pausar" : "Activar"}
        </button>
        <button class="small danger" data-remove="${escapeHtml(account.handle)}">Eliminar</button>
      </div>
    </div>
  `).join("");
}

/* ------------------------------------------------------------------ */
/* Bandeja                                                             */
/* ------------------------------------------------------------------ */
async function loadTweets() {
  const status = $("filter-status").value;
  const handle = $("filter-account").value;
  const limit = $("filter-limit").value;
  const pending = $("filter-pending").value;
  const query = new URLSearchParams({ status, limit, pending });
  if (handle) query.set("handle", handle);
  const payload = await api(`/api/tweets?${query.toString()}`);
  app.tweets = payload.tweets || [];
  renderCounts(payload.counts);
  renderInbox(app.tweets);
}

/* ------------------------------------------------------------------ */
/* Bandeja: lista cronológica agrupada por día                          */
/* ------------------------------------------------------------------ */
function sortKey(tweet) {
  return tweet.posted_at || tweet.fetched_at || "";
}

/** Resta días a una fecha ISO `YYYY-MM-DD`. */
function shiftIsoDay(isoDate, delta) {
  const parts = String(isoDate || "").split("-").map(Number);
  if (parts.length !== 3 || parts.some((value) => !Number.isFinite(value))) return "";
  const moment = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  moment.setUTCDate(moment.getUTCDate() + delta);
  return moment.toISOString().slice(0, 10);
}

/** «01/10/2026» -> «2026-10-01». */
function shortToIso(shortDate) {
  const parts = String(shortDate || "").split("/");
  return parts.length === 3 ? `${parts[2]}-${parts[1]}-${parts[0]}` : "";
}

function dayLabel(shortDate) {
  const iso = shortToIso(shortDate);
  if (!iso) return "Sin fecha";
  if (app.today && iso === app.today) return "Hoy";
  if (app.today && iso === shiftIsoDay(app.today, -1)) return "Ayer";
  const parts = shortDate.split("/");
  const months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const month = months[Number(parts[1]) - 1] || parts[1];
  return `${Number(parts[0])} ${month} ${parts[2]}`;
}

function tweetCard(tweet) {
  // Se usan las miniaturas reducidas: 14 KB frente a 99 KB por imagen.
  const sources = tweet.thumbs && tweet.thumbs.length ? tweet.thumbs : (tweet.media || []);
  const thumbs = sources.slice(0, 6).map((url) => (
    `<img src="${escapeHtml(url)}" alt="" loading="lazy" referrerpolicy="no-referrer"
          onerror="this.style.display='none'">`
  )).join("");
  const noMedia = !(tweet.media || []).length;
  const processing = tweet.status === "procesando";
  // Botón «Procesar» siempre; «Abrir editor» solo cuando ya hay tarjeta.
  const editorButton = tweet.has_card
    ? `<button class="small accent" data-editor="${escapeHtml(String(tweet.card_id))}">Abrir editor</button>`
    : "";
  const processButton = processing
    ? `<button class="small" disabled>${spinner("procesando")} procesando…</button>`
    : `<button class="small primary" data-process="${escapeHtml(tweet.tweet_id)}"
               ${noMedia ? "disabled title='Sin imágenes'" : ""}>${tweet.is_processed ? "Reprocesar" : "Procesar"}</button>`;
  const processed = tweet.is_processed
    ? `<span class="done-flag">${ICON.visto} procesada</span>`
    : "";
  const duplicate = tweet.is_duplicate
    ? `<span class="pill" style="border-color:#4a4658">repetida${tweet.duplicate_of ? ` de ${escapeHtml(tweet.duplicate_of)}` : ""}</span>`
    : "";
  const classes = ["tweet"];
  if (app.selectedId === tweet.tweet_id) classes.push("selected");
  if (tweet.is_processed) classes.push("processed");
  if (tweet.is_duplicate) classes.push("is-duplicate");

  return `
    <div class="${classes.join(" ")}">
      <div class="meta">
        <span class="badge ${escapeHtml(tweet.status)}">${escapeHtml(tweet.status)}</span>
        ${processed}${duplicate}
        <span class="account">@${escapeHtml(tweet.author_handle || tweet.source_handle)}</span>
        ${tweet.author_handle && tweet.author_handle !== tweet.source_handle
          ? `<span class="muted">vía @${escapeHtml(tweet.source_handle)}</span>` : ""}
      </div>
      <div class="when">
        <strong data-posted="${escapeHtml(tweet.posted_at || tweet.fetched_at || "")}">${escapeHtml(tweet.posted_relative || "sin fecha")}</strong>
        <span class="muted">· ${escapeHtml(tweet.posted_absolute || "")}</span>
        ${tweet.date_is_estimated ? `<span class="muted" title="La fuente no dio la fecha exacta; se usa la de descarga.">(aprox.)</span>` : ""}
      </div>
      <div class="text">${escapeHtml(tweet.text || "(sin texto)")}</div>
      ${noMedia
        ? `<div class="small" style="color:var(--warn)">Sin imágenes: el compositor necesita al menos una para crear la tarjeta.</div>`
        : `<div class="thumbs">${thumbs}</div>`}
      <div class="actions">
        ${processButton}
        ${editorButton}
        ${tweet.url ? `<a class="small" href="${escapeHtml(tweet.url)}" target="_blank" rel="noopener">Ver original</a>` : ""}
        <button class="small ghost" data-status="${escapeHtml(tweet.tweet_id)}" data-value="descartado">Descartar</button>
      </div>
    </div>`;
}

function renderInbox(tweets) {
  if (!tweets.length) {
    $("inbox").innerHTML = `<div class="empty">No hay publicaciones con este filtro.
      Añade cuentas y pulsa «Buscar ahora».</div>`;
    return;
  }

  // Se reordena aquí también, por si algún día el origen cambia el criterio:
  // la promesa de la interfaz es «más reciente primero».
  const ordered = [...tweets].sort((a, b) => sortKey(b).localeCompare(sortKey(a)));

  const blocks = [];
  let currentDay = null;
  let buffer = [];
  let count = 0;

  const flush = () => {
    if (!buffer.length) return;
    blocks.push(
      `<div class="feed-day">
         <span class="label">${escapeHtml(dayLabel(currentDay))}</span>
         <span class="count">${count} publicación(es)</span>
         <span class="line"></span>
       </div>` + buffer.join("")
    );
    buffer = [];
    count = 0;
  };

  for (const tweet of ordered) {
    const day = tweet.posted_short || "";
    if (day !== currentDay) {
      flush();
      currentDay = day;
    }
    buffer.push(tweetCard(tweet));
    count += 1;
  }
  flush();
  $("inbox").innerHTML = blocks.join("");
}

/* ------------------------------------------------------------------ */
/* Procesar: se encola y se sigue, sin dejar al navegador esperando    */
/* ------------------------------------------------------------------ */
async function processTweet(tweetId, button) {
  await withBusy(button, "Encolando…", async () => {
    try {
      const payload = await api(`/api/tweets/${tweetId}/process`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      const job = payload.job || {};
      toast(
        `Procesando en segundo plano (trabajo ${job.id}). Puedes seguir usando el ` +
        "dashboard o bloquear el móvil: el trabajo continúa en el servidor."
      );
      await loadTweets();
      const finished = await waitForJob(job.id);
      await loadTweets();
      await loadState();
      if (finished && finished.state === "hecho") {
        toast("Tarjeta lista. Pulsa «Abrir editor» en la publicación.");
      }
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

/**
 * Sigue un trabajo hasta que termina.
 *
 * El trabajo vive en el servidor: da igual que el móvil se bloquee o pierda la
 * conexión un momento, porque al volver la tarjeta ya está hecha.
 */
async function waitForJob(jobId) {
  for (let attempt = 0; attempt < 400; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    let job = null;
    try {
      job = (await api(`/api/jobs/${jobId}`)).job;
    } catch (error) {
      continue;   // un fallo puntual de red no cancela el trabajo
    }
    if (!job) continue;
    if (job.state === "hecho") return job;
    if (job.state === "fallido") {
      toast(job.detail || "El trabajo falló en el servidor.", "error");
      return job;
    }
  }
  toast("El trabajo sigue en marcha; la bandeja se actualizará sola.", "error");
  return null;
}

function openEditor(cardId) {
  // Editor independiente: pestaña propia.
  window.open(`/editor.html?card=${encodeURIComponent(cardId)}`, "_blank", "noopener");
}

/* ------------------------------------------------------------------ */
/* Editor                                                              */
/* ------------------------------------------------------------------ */
async function openTweet(tweetId) {
  app.selectedId = tweetId;
  const payload = await api(`/api/tweets/${tweetId}`);
  const tweet = payload.tweet;
  const card = payload.card;
  app.card = card;

  $("editor-empty").hidden = true;
  $("editor-body").hidden = false;
  switchTab("editor");

  $("editor-tweet").innerHTML = `
    <div class="meta row small muted" style="margin-bottom:8px">
      <span class="badge ${escapeHtml(tweet.status)}">${escapeHtml(tweet.status)}</span>
      <span class="account">@${escapeHtml(tweet.author_handle || tweet.source_handle)}</span>
      <span>${escapeHtml(tweet.posted_at ? formatDate(tweet.posted_at) : (tweet.relative_time || ""))}</span>
      ${tweet.url ? `<a href="${escapeHtml(tweet.url)}" target="_blank" rel="noopener">original</a>` : ""}
    </div>
    <div class="text">${escapeHtml(tweet.text || "(sin texto)")}</div>
    <div class="thumbs" style="margin-top:9px">
      ${((tweet.thumbs && tweet.thumbs.length ? tweet.thumbs : (tweet.media || [])).slice(0, 8)).map((url) => (
        `<img src="${escapeHtml(url)}" alt="" loading="lazy" referrerpolicy="no-referrer"
              onerror="this.style.display='none'">`
      )).join("")}
    </div>`;

  const params = (card && card.params) || payload.defaults || {};
  $("f-top").value = params.top || "";
  $("f-bottom").value = params.bottom || "";
  $("f-caption").value = params.caption || "";
  if (params.format) $("f-format").value = params.format;
  if (params.fit) $("f-fit").value = params.fit;
  if (params.style) $("f-style").value = params.style;
  if (params.resolution) $("f-resolution").value = params.resolution;
  if (params.backend) $("f-backend").value = params.backend;

  const analysis = tweet.analysis;
  $("analysis-info").innerHTML = analysis
    ? `<span class="pill ok">${escapeHtml(analysis.provider)}</span>
       <div class="muted" style="margin-top:6px">${escapeHtml(analysis.reasoning || "")}</div>
       <div class="muted">Hashtags: ${escapeHtml((analysis.hashtags || []).join(" "))}</div>`
    : `<span class="muted">Sin analizar. «Analizar con IA» propone titular, contexto y caption.</span>`;

  if (card) {
    showCard(card);
  } else {
    app.card = null;
    $("preview").innerHTML = `<span class="muted">Todavía no hay tarjeta generada.</span>`;
    $("preview-meta").textContent = "";
    $("download-link").hidden = true;
  }
  $("send-result").textContent = "";
  renderInbox(app.tweets);
}

function currentParams() {
  return {
    top: $("f-top").value,
    bottom: $("f-bottom").value,
    format: $("f-format").value,
    fit: $("f-fit").value,
    style: $("f-style").value,
    resolution: $("f-resolution").value,
    backend: $("f-backend").value,
    caption: $("f-caption").value,
  };
}

function showCard(card) {
  app.card = card;
  const meta = card.meta || {};
  // Vista previa reducida: el PNG original ronda los 30 MB y aquí se muestra
  // a unos cientos de píxeles.
  const src = `/api/cards/${card.id}/image?size=preview&w=1440&v=${encodeURIComponent(card.output_path || card.id)}`;
  $("preview").innerHTML = `<img src="${src}" alt="Tarjeta generada">`;
  $("preview-meta").textContent =
    `v${card.version} · ${meta.width || "?"}×${meta.height || "?"} · ${meta.output_format || ""} · ` +
    `${meta.style || ""} · ${meta.resolution || ""} · ${meta.images || 0} imagen(es)`;
  $("render-info").textContent = `Tarjeta v${card.version} lista`;
  $("btn-render").disabled = false;
  $("btn-send").disabled = false;
  const link = $("download-link");
  link.href = src;
  link.download = `tarjeta-${card.tweet_id}-v${card.version}.png`;
  link.hidden = false;
}

/* ------------------------------------------------------------------ */
/* Acciones                                                            */
/* ------------------------------------------------------------------ */
async function poll() {
  const button = $("btn-poll");
  await withBusy(button, "Buscando…", async () => {
    try {
      const response = await api("/api/poll", {
        method: "POST",
        body: JSON.stringify({ background: true }),
      });
      if (!response.started) {
        toast("El sondeo no llegó a arrancar.", "error");
        return;
      }
      toast("Sondeo iniciado. Se abrirá Chrome para leer las cuentas; puede tardar.");
      await waitForPoller();
      await loadTweets();
      await loadState();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function waitForPoller() {
  let sawActivity = false;
  for (let attempt = 0; attempt < 400; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    let state;
    try {
      state = await api("/api/state");
    } catch (error) {
      continue;
    }
    const poller = state.poller || {};
    const progress = poller.progress || {};
    if (poller.busy) {
      sawActivity = true;
      $("btn-poll").innerHTML =
        `${spinner("sondeando")} ${progress.done || 0}/${progress.total || "?"}`;
      if (progress.current) {
        $("render-info") && ($("render-info").textContent = `Leyendo @${progress.current}…`);
      }
      continue;
    }
    if (sawActivity || !poller.busy) {
      const last = poller.last_run;
      if (last && typeof last.new === "number") {
        toast(`Sondeo terminado: ${last.new} publicación(es) nueva(s).`);
      } else if (last && last.skipped) {
        toast(`Sondeo omitido: ${last.skipped}.`);
      }
      if (poller.last_error) toast(poller.last_error, "error");
      renderCounts(state.counts);
      return;
    }
  }
  toast("El sondeo sigue en marcha; revisa la bitácora en un momento.", "error");
}

/**
 * Explica qué formato se va a usar y por qué.
 *
 * Antes el formato lo imponía la sugerencia de la IA para cada publicación, así
 * que unas tarjetas salían verticales, otras cuadradas y otras horizontales
 * sin que se pudiera pedir vertical de forma fiable.
 */
function formatNotice(defaults) {
  if (!defaults || !defaults.format) return "";
  if (!defaults.format_is_forced) {
    return defaults.suggested_format
      ? `<div class="muted" style="margin-top:6px">Formato automático: la IA propone ` +
        `<strong>${escapeHtml(defaults.suggested_format)}</strong>. Elige uno concreto ` +
        `para que se respete siempre.</div>`
      : "";
  }
  const aviso = defaults.suggested_format &&
    defaults.suggested_format !== defaults.format
    ? ` La IA proponía ${escapeHtml(defaults.suggested_format)} y se ignora.`
    : "";
  return (
    `<div class="muted" style="margin-top:6px">Formato fijado en ` +
    `<strong>${escapeHtml(defaults.format)}</strong> por el ajuste ` +
    `<code>DASHBOARD_DEFAULT_FORMAT</code>.` + aviso + `</div>`
  );
}

async function analyzeSelected() {
  if (!app.selectedId) return toast("Selecciona una publicación primero.", "error");
  const button = $("btn-analyze");
  await withBusy(button, "Analizando…", async () => {
    try {
      const payload = await api(`/api/tweets/${app.selectedId}/analyze`, {
        method: "POST",
        body: JSON.stringify({ provider: $("f-analysis").value || null }),
      });
      const defaults = payload.defaults || {};
      $("f-top").value = defaults.top || "";
      $("f-bottom").value = defaults.bottom || "";
      $("f-caption").value = defaults.caption || "";
      if (defaults.format) $("f-format").value = defaults.format;
      const analysis = payload.tweet.analysis || {};
      $("analysis-info").innerHTML =
        `<span class="pill ok">${escapeHtml(analysis.provider)}</span>` +
        `<div class="muted" style="margin-top:6px">${escapeHtml(analysis.reasoning || "")}</div>` +
        formatNotice(defaults);
      toast("Análisis aplicado al formulario.");
      await loadTweets();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function generateCard() {
  if (!app.selectedId) return toast("Selecciona una publicación primero.", "error");
  const button = $("btn-generate");
  await withBusy(button, "Componiendo…", async () => {
    try {
      const payload = await api(`/api/tweets/${app.selectedId}/card`, {
        method: "POST",
        body: JSON.stringify({ params: currentParams() }),
      });
      showCard(payload.card);
      toast("Tarjeta generada y validada.");
      await loadTweets();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function renderCard() {
  if (!app.card) return toast("Genera la tarjeta primero.", "error");
  const button = $("btn-render");
  await withBusy(button, "Recomponiendo…", async () => {
    try {
      const payload = await api(`/api/cards/${app.card.id}/render`, {
        method: "POST",
        body: JSON.stringify({ params: currentParams() }),
      });
      showCard(payload.card);
      toast("Tarjeta recompuesta con los cambios.");
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function sendCard() {
  if (!app.card) return toast("Genera la tarjeta primero.", "error");
  const button = $("btn-send");
  await withBusy(button, "Enviando…", async () => {
    try {
      const payload = await api(`/api/cards/${app.card.id}/send`, {
        method: "POST",
        body: JSON.stringify({
          caption: $("f-caption").value,
          provider: $("f-delivery").value,
        }),
      });
      const outcome = payload.delivery || {};
      $("send-result").innerHTML =
        `<span class="pill ok">enviado</span> ${escapeHtml(outcome.method || "")}` +
        (outcome.message_id ? ` · mensaje ${escapeHtml(String(outcome.message_id))}` : "") +
        (outcome.path ? ` · <code>${escapeHtml(outcome.path)}</code>` : "");
      toast("Entrega completada.");
      await loadTweets();
      await loadState();
    } catch (error) {
      $("send-result").innerHTML = `<span class="pill off">error</span> ${escapeHtml(error.message)}`;
      toast(error.message, "error");
    }
  });
}

/* ------------------------------------------------------------------ */
/* Navegación                                                          */
/* ------------------------------------------------------------------ */
/**
 * Convierte el marcado de color del compositor en texto con color de verdad.
 *
 * Las tarjetas guardan «{PALABRA|FF7A00}» para pintar palabras sueltas. En un
 * campo de edición debe verse el marcado, porque es lo que se edita; pero en
 * una vista de lectura, mostrarlo en crudo parece un error de la aplicación.
 */
function renderColoredText(markup) {
  const texto = String(markup || "");
  const patron = /\{([^{}|]+)\|([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\}/g;
  let salida = "";
  let ultimo = 0;
  let coincidencia = patron.exec(texto);
  while (coincidencia !== null) {
    salida += escapeHtml(texto.slice(ultimo, coincidencia.index));
    salida +=
      `<span style="color:#${coincidencia[2]}">` +
      `${escapeHtml(coincidencia[1])}</span>`;
    ultimo = coincidencia.index + coincidencia[0].length;
    coincidencia = patron.exec(texto);
  }
  return salida + escapeHtml(texto.slice(ultimo));
}

/* ------------------------------------------------------------------ */
/* Procesadas: donde viven las tarjetas ya creadas                     */
/* ------------------------------------------------------------------ */
async function loadCards() {
  const payload = await api("/api/cards?limit=200");
  app.cards = payload.cards || [];
  renderCards(app.cards);
}

function renderCards(cards) {
  const box = $("cards");
  const contador = $("cards-count");
  if (!box) return;
  if (contador) {
    contador.textContent = cards.length
      ? `${cards.length} tarjeta(s) · ${cards.filter((c) => c.sent).length} enviada(s)`
      : "";
  }
  if (!cards.length) {
    box.innerHTML = `<div class="empty">Todavía no has procesado ninguna publicación.
      Procesa alguna desde la bandeja y aparecerá aquí.</div>`;
    return;
  }

  box.innerHTML = cards.map((item) => {
    const card = item.card || {};
    const tweet = item.tweet || {};
    const meta = card.meta || {};
    const params = card.params || {};
    // Miniatura reducida, no el PNG de 30 MB: en una lista de móvil el
    // original es una imagen de 4000 px en un hueco de 400 px.
    const sello = encodeURIComponent(`${card.output_path || card.id}-${card.version || ""}`);
    const src = `/api/cards/${card.id}/image?size=preview&w=720&v=${sello}`;
    const src2x = `/api/cards/${card.id}/image?size=preview&w=1440&v=${sello}`;
    const descarga = `/api/cards/${card.id}/image?size=full`;
    // Se reserva el espacio de la imagen con su proporción real: si no, al
    // cargar empuja el contenido y la lista da un salto (CLS).
    const ancho = Number(meta.width) || 0;
    const alto = Number(meta.height) || 0;
    const proporcion = ancho > 0 && alto > 0 ? `aspect-ratio: ${ancho} / ${alto};` : "";
    const estado = item.sent
      ? `<span class="pill ok"><span class="dot"></span>enviada</span>`
      : `<span class="pill">sin enviar</span>`;
    const entrega = item.last_delivery
      ? `<span class="small muted">última entrega: ${escapeHtml(item.last_delivery.status)} ` +
        `${escapeHtml(formatDate(item.last_delivery.created_at))}</span>`
      : "";
    return `
      <div class="tweet processed">
        <div class="meta">
          ${estado}
          <span class="badge tarjeta_lista">v${escapeHtml(String(card.version))}</span>
          <span class="account">@${escapeHtml(tweet.author_handle || tweet.source_handle || "")}</span>
          <span class="muted">${escapeHtml(tweet.posted_relative || "")}</span>
        </div>
        <a href="${escapeHtml(item.editor_url)}" target="_blank" rel="noopener" class="card-shot">
          <img src="${src}" srcset="${src} 1x, ${src2x} 2x"
               alt="Tarjeta generada de @${escapeHtml(tweet.author_handle || "")}"
               loading="lazy" decoding="async" width="${ancho || ""}" height="${alto || ""}"
               style="width:100%;border-radius:10px;border:1px solid var(--line);${proporcion}">
        </a>
        <div class="small"><strong>${renderColoredText(params.top)}</strong></div>
        <div class="small muted">${renderColoredText(params.bottom)}</div>
        <div class="small muted">${escapeHtml(String(meta.width || "?"))}×${escapeHtml(String(meta.height || "?"))}
          · ${escapeHtml(String(meta.output_format || ""))}</div>
        ${entrega}
        ${formatPicker(card)}
        <div class="actions">
          <button class="small accent" data-editor="${escapeHtml(String(card.id))}">Abrir editor</button>
          <button class="small" data-regen="${escapeHtml(String(card.id))}">Regenerar texto</button>
          <button class="small primary" data-send="${escapeHtml(String(card.id))}">Enviar a Telegram</button>
          <a class="small" href="${descarga}" download="tarjeta-${escapeHtml(String(card.id))}.png">Descargar</a>
        </div>
      </div>`;
  }).join("");
}

/**
 * Selector de formato con su botón, para corregir una tarjeta ya hecha.
 *
 * Antes, si una tarjeta salía cuadrada u horizontal, no había forma de
 * arreglarla desde aquí: había que abrir el editor de esa publicación.
 */
function formatPicker(card) {
  const actual = String((card.params || {}).format || "");
  const opciones = [
    ["9:16", "9:16 vertical"],
    ["1:1", "1:1 cuadrado"],
    ["16:9", "16:9 horizontal"],
  ];
  const lista = opciones
    .map(([valor, texto]) =>
      `<option value="${valor}"${valor === actual ? " selected" : ""}>${texto}</option>`)
    .join("");
  return `
    <div class="format-row">
      <label class="small muted" for="fmt-${card.id}">Formato</label>
      <select id="fmt-${card.id}" data-format-for="${escapeHtml(String(card.id))}">${lista}</select>
      <button class="small" data-recompose="${escapeHtml(String(card.id))}">Cambiar formato</button>
    </div>`;
}

/** Recompone una tarjeta con el formato elegido. */
async function recomposeCard(cardId, button) {
  const select = document.querySelector(`[data-format-for="${cardId}"]`);
  const formato = select ? select.value : "9:16";
  await withBusy(button, "Encolando…", async () => {
    try {
      const payload = await api(`/api/cards/${cardId}/render`, {
        method: "POST",
        body: JSON.stringify({ params: { format: formato } }),
      });
      toast(`Recomponiendo en ${formato} (trabajo ${payload.job.id}).`);
      const finished = await waitForJob(payload.job.id);
      await loadCards();
      if (finished && finished.state === "hecho") {
        toast(`Tarjeta recompuesta en ${formato}.`);
      }
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

/** Regenera el texto de una tarjeta ya creada (encolado, sin bloquear). */
async function regenerateCard(cardId, button) {
  await withBusy(button, "Encolando…", async () => {
    try {
      const payload = await api(`/api/cards/${cardId}/regenerate-text`, {
        method: "POST",
        body: JSON.stringify({ render: true }),
      });
      toast(`Regenerando el texto en segundo plano (trabajo ${payload.job.id}).`);
      const finished = await waitForJob(payload.job.id);
      await loadCards();
      if (finished && finished.state === "hecho") {
        toast("Texto y colores regenerados. Abre el editor para revisarlos.");
      }
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

/** Envía una tarjeta a Telegram (encolado: el PNG pesa mucho). */
async function sendCardFromList(cardId, button) {
  await withBusy(button, "Encolando…", async () => {
    try {
      const payload = await api(`/api/cards/${cardId}/send`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      toast(`Enviando en segundo plano (trabajo ${payload.job.id}).`);
      const finished = await waitForJob(payload.job.id);
      await loadCards();
      if (finished && finished.state === "hecho") {
        toast("Tarjeta entregada.");
      }
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

const TABS = ["inbox", "cards", "editor", "accounts", "settings", "log"];

/** Pestaña indicada en la dirección; permite recargar sin perder el sitio. */
function tabFromHash() {
  const name = String(window.location.hash || "").replace("#", "").trim();
  return TABS.includes(name) ? name : "inbox";
}

function switchTab(name, updateHash = true) {
  const destino = TABS.includes(name) ? name : "inbox";
  for (const button of document.querySelectorAll("nav.tabs button")) {
    const activo = button.dataset.tab === destino;
    button.classList.toggle("active", activo);
    // Para lectores de pantalla, además del color.
    if (activo) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  for (const section of document.querySelectorAll("main > section")) {
    section.hidden = section.id !== `tab-${destino}`;
  }
  // La dirección refleja la pestaña: al recargar o volver atrás no se pierde.
  if (updateHash && window.location.hash !== `#${destino}`) {
    try {
      window.history.replaceState(null, "", `#${destino}`);
    } catch (error) {
      window.location.hash = destino;
    }
  }
  // La pestaña de procesadas se refresca al entrar, para no quedarse obsoleta.
  if (destino === "cards") {
    loadCards().catch((error) => toast(error.message, "error"));
  }
}

window.addEventListener("hashchange", () => switchTab(tabFromHash(), false));

/* ------------------------------------------------------------------ */
/* Eventos                                                             */
/* ------------------------------------------------------------------ */
document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, a");
  if (!target) return;
  const dataset = target.dataset || {};

  if (dataset.tab) return switchTab(dataset.tab);
  if (target.id === "btn-poll") return poll();
  if (target.id === "btn-refresh") return refreshAll();
  if (target.id === "btn-chatgpt-login") return openChatgptLogin();
  if (target.id === "btn-analysis-test") return testAnalysis();
  if (target.id === "btn-purge") return purge();
  if (target.id === "btn-refresh-cards") return loadCards().catch((e) => toast(e.message, "error"));

  if (dataset.regen) return regenerateCard(dataset.regen, target);
  if (dataset.recompose) return recomposeCard(dataset.recompose, target);
  if (dataset.send) return sendCardFromList(dataset.send, target);
  if (target.id === "btn-analyze") return analyzeSelected();
  if (target.id === "btn-generate") return generateCard();
  if (target.id === "btn-render") return renderCard();
  if (target.id === "btn-send") return sendCard();

  if (dataset.process) {
    return processTweet(dataset.process, target);
  }

  if (dataset.editor) {
    return openEditor(dataset.editor);
  }

  if (dataset.open) return openTweet(dataset.open);

  if (dataset.analyze) {
    await withBusy(target, "Analizando…", async () => {
      try {
        await api(`/api/tweets/${dataset.analyze}/analyze`, {
          method: "POST",
          body: JSON.stringify({ provider: null }),
        });
        toast("Análisis guardado.");
        await loadTweets();
      } catch (error) {
        toast(error.message, "error");
      }
    });
    return;
  }

  if (dataset.status) {
    try {
      await api(`/api/tweets/${dataset.status}/status`, {
        method: "POST",
        body: JSON.stringify({ status: dataset.value }),
      });
      toast("Estado actualizado.");
      await loadTweets();
    } catch (error) {
      toast(error.message, "error");
    }
    return;
  }

  if (dataset.remove) {
    if (!confirm(`¿Eliminar @${dataset.remove} del seguimiento?`)) return;
    try {
      await api(`/api/accounts/${encodeURIComponent(dataset.remove)}`, { method: "DELETE" });
      toast("Cuenta eliminada.");
      await refreshAll();
    } catch (error) {
      toast(error.message, "error");
    }
    return;
  }

  if (dataset.toggle) {
    try {
      await api(`/api/accounts/${encodeURIComponent(dataset.toggle)}/active`, {
        method: "POST",
        body: JSON.stringify({ active: dataset.active !== "1" }),
      });
      await refreshAll();
    } catch (error) {
      toast(error.message, "error");
    }
  }
});

$("form-account").addEventListener("submit", async (event) => {
  event.preventDefault();
  const handle = $("account-handle").value.trim();
  if (!handle) return;
  try {
    await api("/api/accounts", { method: "POST", body: JSON.stringify({ handle }) });
    $("account-handle").value = "";
    toast("Cuenta añadida.");
    await refreshAll();
  } catch (error) {
    toast(error.message, "error");
  }
});

for (const id of ["filter-status", "filter-account", "filter-limit", "filter-pending"]) {
  $(id).addEventListener("change", () => loadTweets().catch((error) => toast(error.message, "error")));
}

/* ------------------------------------------------------------------ */
/* Arranque                                                            */
/* ------------------------------------------------------------------ */
async function refreshAll() {
  try {
    await loadState();
    await loadTweets();
    // Se respeta la pestaña de la dirección al arrancar.
    switchTab(tabFromHash(), false);
    if (app.selectedId) await openTweet(app.selectedId);
    if (tabFromHash() === "cards") await loadCards();
  } catch (error) {
    toast(error.message, "error");
  }
}

refreshAll();
// Refresco ligero: contadores, bitácora, estado del sondeo y **las horas
// relativas**, que antes se quedaban congeladas.
setInterval(() => {
  api("/api/state").then((state) => {
    rememberServerClock(state.clock);
    renderCounts(state.counts);
    renderEvents(state.events);
    renderPollerState(state);
    renderMaintenance(state);
    tickRelativeTimes();
  }).catch(() => {});
}, 15000);

// Las etiquetas de tiempo avanzan por su cuenta, sin pedir nada al servidor.
setInterval(tickRelativeTimes, 20000);
