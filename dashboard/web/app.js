/* EditImg · visor de publicaciones — JavaScript puro, sin dependencias.
 *
 * Este dashboard solo sirve para **mirar** lo que publican las cuentas y
 * marcarlo. No compone tarjetas, no analiza texto y no entrega nada: todo eso
 * se quitó, y con ello la mitad de los botones que había.
 */
"use strict";

const $ = (id) => document.getElementById(id);

/** Iconos en SVG, no emoji: heredan el color y los lee bien un lector. */
const ICON = {
  reloj:
    `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" ` +
    `stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false">` +
    `<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/></svg>`,
  visto:
    `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ` +
    `stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ` +
    `focusable="false"><path d="M20 6L9 17l-5-5"/></svg>`,
  video:
    `<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true" ` +
    `focusable="false"><path d="M8 5.5v13l11-6.5z"/></svg>`,
};

function spinner(label) {
  return `<span class="spinner" role="status" aria-label="${escapeHtml(label)}"></span>`;
}

const app = {
  state: null,
  tweets: [],
  ready: new Set(),
  today: null,
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
    throw new Error((payload && payload.error) || `HTTP ${response.status}`);
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

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** Hora actual según el reloj verificado, avanzada con el del navegador. */
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
  if (seconds < 0) seconds = 0;
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
  return Promise.resolve().then(task).finally(() => {
    if (button) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.textContent = original;
    }
  });
}

/**
 * Copia texto al portapapeles, con respaldo para HTTP.
 *
 * `navigator.clipboard` **solo existe en contexto seguro** (HTTPS o
 * localhost). Al entrar por `http://100.95.55.79:8765` desde el móvil no está
 * disponible, así que se recurre a un campo temporal con `execCommand`, que es
 * lo único que funciona ahí. Si tampoco, se enseña el enlace para copiarlo a
 * mano en vez de dejar al usuario sin nada.
 */
async function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      /* se intenta el respaldo */
    }
  }
  try {
    const campo = document.createElement("textarea");
    campo.value = text;
    campo.setAttribute("readonly", "");
    campo.style.position = "fixed";
    campo.style.top = "-1000px";
    document.body.appendChild(campo);
    campo.select();
    campo.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    campo.remove();
    if (ok) return true;
  } catch (error) {
    /* se cae al aviso */
  }
  window.prompt("Copia el enlace:", text);
  return false;
}

/* ------------------------------------------------------------------ */
/* Estado general                                                      */
/* ------------------------------------------------------------------ */
async function loadState() {
  const state = await api("/api/state");
  app.state = state;
  app.today = ((state.clock && state.clock.now_local) || "").slice(0, 10) || null;
  rememberServerClock(state.clock);
  renderProviderPills(state);
  renderCounts(state.counts);
  renderAccounts(state.accounts);
  renderProviders(state);
  renderSettings(state.settings);
  renderMaintenance(state);
  renderPollerState(state);
  renderEvents(state.events);
  fillAccountFilter(state.accounts);
}

/**
 * Estado de las fuentes, agrupado por familia.
 *
 * Antes se dibujaba una píldora por proveedor (diez en total) dentro de una
 * tira deslizable que solo dejaba ver la primera y media.
 */
function renderProviderPills(state) {
  const groups = [["Descubrir", state.timeline_providers]];
  const parts = [];
  for (const [label, list] of groups) {
    const providers = list || [];
    if (!providers.length) continue;
    const ready = providers.filter((provider) => provider.available).length;
    const cls = ready === providers.length ? "ok" : ready ? "warn" : "off";
    const detail = providers
      .map((p) => `${p.available ? "listo" : "no"} · ${p.name}: ${p.detail || "sin detalle"}`)
      .join("\n");
    parts.push(
      `<span class="pill ${cls}" title="${escapeHtml(detail)}">` +
      `<span class="dot"></span>${escapeHtml(label)} ${ready}/${providers.length}</span>`
    );
  }
  const clock = state.clock;
  if (clock) {
    const skewed = Math.abs(clock.offset_seconds || 0) > 60;
    parts.push(
      `<span class="pill ${skewed ? "warn" : "ok"}" title="${escapeHtml(
        skewed
          ? "Las horas se calculan contra servidores públicos de hora, no contra el reloj del sistema."
          : "El reloj del sistema coincide con la hora de referencia."
      )}">` +
      `<span class="dot"></span>${ICON.reloj} ${escapeHtml(clock.local_offset_human || "UTC")}` +
      (skewed ? ` · ${escapeHtml(clock.offset_human || "")}` : "") +
      `</span>`
    );
  }
  $("provider-pills").innerHTML = parts.join("");
}

/**
 * Nombre legible de cada estado.
 *
 * Los heredados se marcan como «(antes)»: son publicaciones que se convirtieron
 * en tarjeta con el flujo anterior. Sin esto el contador enseñaba la clave
 * interna tal cual («tarjeta_lista: 8»), que no significa nada para nadie.
 */
const COUNT_LABELS = {
  nuevo: "pendientes",
  listo: "listas",
  descartado: "descartadas",
  fallido: "fallidas",
  duplicado: "repetidas",
  tarjeta_lista: "con tarjeta (antes)",
  enviado: "enviadas (antes)",
  analizado: "analizadas (antes)",
  seleccionado: "seleccionadas (antes)",
  procesando: "procesando",
};

/** Contadores: solo los que tienen valor, para no llenar de ceros. */
function renderCounts(counts) {
  if (!counts) return;
  const box = $("counts");
  if (!box) return;
  const partes = Object.entries(counts)
    .filter(([, value]) => Number(value) > 0)
    .map(([key, value]) =>
      `<span class="pill">${escapeHtml(COUNT_LABELS[key] || key)}: ${value}</span>`);
  box.innerHTML = partes.length ? partes.join("") : `<span class="pill">sin publicaciones</span>`;
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
  for (const provider of state.timeline_providers || []) {
    rows.push(
      `<div class="row" style="margin-bottom:6px">` +
      `<span class="pill ${provider.available ? "ok" : "off"}">${escapeHtml(provider.name)}</span>` +
      `<span class="small muted">${escapeHtml(provider.detail || "")}</span></div>`
    );
  }
  if (state.profiles) {
    rows.push(
      `<p class="small muted" style="margin-top:12px">Perfil del navegador: ` +
      `<code>${escapeHtml(state.profiles.x || "")}</code></p>`
    );
  }
  $("providers").innerHTML = rows.join("");
}

function renderMaintenance(state) {
  const box = $("maintenance-state");
  if (!box) return;
  const info = state.maintenance || {};
  const due = info.purge_due
    ? `<span class="pill warn">toca limpiar</span>`
    : `<span class="pill ok">al día</span>`;
  box.innerHTML =
    `<span class="pill">retención: ${escapeHtml(String(info.retention_hours))} h</span>` +
    `<span class="pill ok">${escapeHtml(String(info.total_seen || 0))} vistas recordadas</span>` +
    due +
    `<span class="small muted">última limpieza: ${escapeHtml(info.last_purge_at ? formatDate(info.last_purge_at) : "nunca")}</span>`;
}

function renderSettings(settings) {
  if (!settings) return;
  const interesting = [
    ["Fuente de publicaciones", settings.timeline_provider],
    ["Intervalo de sondeo", `${settings.poll_interval_seconds} s`],
    ["Sondeo al arrancar", settings.poll_on_start ? "sí" : "no"],
    ["Instancias Nitter", (settings.nitter_instances || []).join(", ")],
    ["Navegador", `${settings.browser_channel} · headless: ${settings.browser_headless}`],
    ["Retención", `${settings.retention_hours} h`],
  ];
  $("settings").innerHTML = interesting
    .map(([key, value]) => (
      `<div class="row" style="justify-content:space-between;border-bottom:1px solid var(--line-soft);padding:5px 0">` +
      `<span class="small muted">${escapeHtml(key)}</span>` +
      `<span class="small">${escapeHtml(value)}</span></div>`
    )).join("");
}

function renderPollerState(state) {
  const box = $("poller-state");
  if (!box) return;
  const poller = state.poller || {};
  const parts = [];
  if (!poller.periodic) {
    parts.push(
      `<span class="pill warn"><span class="dot"></span>búsqueda automática desactivada</span>`,
      `<span class="muted">El botón «Buscar ahora» funciona igual.</span>`
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
  }
  if (poller.last_error) {
    parts.push(`<span class="pill off">último error: ${escapeHtml(poller.last_error)}</span>`);
  }
  box.innerHTML = parts.join(" ");
}

function fillAccountFilter(accounts) {
  const select = $("filter-account");
  const previous = select.value;
  select.innerHTML = `<option value="">Todas</option>` + (accounts || [])
    .map((account) => `<option value="${escapeHtml(account.handle)}">@${escapeHtml(account.handle)}</option>`)
    .join("");
  select.value = previous;
}

function renderAccounts(accounts) {
  const list = accounts || [];
  if (!list.length) {
    $("accounts").innerHTML = `<div class="empty">Todavía no hay cuentas. Añade una arriba.</div>`;
    return;
  }
  $("accounts").innerHTML = list.map((account) => `
    <div class="row" style="border-bottom:1px solid var(--line-soft);padding:8px 0;justify-content:space-between">
      <div>
        <strong>@${escapeHtml(account.handle)}</strong>
        <div class="small muted">
          ${account.active ? "activa" : "pausada"} ·
          última revisión: ${account.last_checked_at ? escapeHtml(formatDate(account.last_checked_at)) : "nunca"}
          ${account.last_error ? ` · <span style="color:var(--off)">${escapeHtml(account.last_error)}</span>` : ""}
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
  const vista = $("filter-view").value;
  const handle = $("filter-account").value;
  const limit = $("filter-limit").value;
  const query = new URLSearchParams({ limit });
  if (vista === "listas") {
    query.set("status", "listo");
    query.set("pending", "0");
  } else if (vista === "todas") {
    query.set("pending", "0");
  } else {
    query.set("pending", "1");
  }
  if (handle) query.set("handle", handle);
  const payload = await api(`/api/tweets?${query.toString()}`);
  app.tweets = payload.tweets || [];
  renderCounts(payload.counts);
  renderInbox(app.tweets);
}

function sortKey(tweet) {
  return tweet.posted_at || tweet.fetched_at || "";
}

function shiftIsoDay(isoDate, delta) {
  const parts = String(isoDate || "").split("-").map(Number);
  if (parts.length !== 3 || parts.some((value) => !Number.isFinite(value))) return "";
  const moment = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  moment.setUTCDate(moment.getUTCDate() + delta);
  return moment.toISOString().slice(0, 10);
}

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

/** Una publicación con sus tres botones: ver, copiar y marcar. */
function tweetCard(tweet) {
  const origen = tweet.thumbs && tweet.thumbs.length ? tweet.thumbs : (tweet.media || []);
  const esVideo = Boolean(tweet.has_video);
  const medios = origen.slice(0, 6).map((url) => (
    `<a href="${escapeHtml(tweet.url || "#")}" target="_blank" rel="noopener" class="shot">
       <img src="${escapeHtml(url)}" alt="" loading="lazy" referrerpolicy="no-referrer"
            onerror="this.parentElement.style.display='none'">
       ${esVideo ? `<span class="video-flag">${ICON.video} vídeo</span>` : ""}
     </a>`
  )).join("");

  const listo = tweet.status === "listo";
  const marca = listo
    ? `<span class="done-flag">${ICON.visto} lista</span>`
    : "";
  const duplicate = tweet.is_duplicate
    ? `<span class="pill">repetida${tweet.duplicate_of ? ` de ${escapeHtml(tweet.duplicate_of)}` : ""}</span>`
    : "";
  const classes = ["tweet"];
  if (listo) classes.push("ready");

  return `
    <div class="${classes.join(" ")}">
      <div class="meta">
        <span class="badge ${escapeHtml(tweet.status)}">${escapeHtml(tweet.status)}</span>
        ${marca}${duplicate}
        <span class="account">@${escapeHtml(tweet.author_handle || tweet.source_handle)}</span>
        ${tweet.author_handle && tweet.author_handle !== tweet.source_handle
          ? `<span class="muted">vía @${escapeHtml(tweet.source_handle)}</span>` : ""}
      </div>
      <div class="when">
        <strong data-posted="${escapeHtml(tweet.posted_at || tweet.fetched_at || "")}">${escapeHtml(tweet.posted_relative || "sin fecha")}</strong>
        <span class="muted">· ${escapeHtml(tweet.posted_absolute || "")}</span>
      </div>
      <div class="text">${escapeHtml(tweet.text || "(sin texto)")}</div>
      ${medios ? `<div class="thumbs">${medios}</div>` : ""}
      <div class="actions">
        ${tweet.url
          ? `<a class="small primary" href="${escapeHtml(tweet.url)}" target="_blank" rel="noopener">Ver original</a>`
          : `<span class="small muted">sin enlace</span>`}
        <button class="small" data-copy="${escapeHtml(tweet.url || "")}"
                ${tweet.url ? "" : "disabled"}>Copiar enlace</button>
        <button class="small ${listo ? "ghost" : "accent"}" data-ready="${escapeHtml(tweet.tweet_id)}"
                data-value="${listo ? "0" : "1"}">${listo ? "Quitar listo" : "Marcar listo"}</button>
      </div>
    </div>`;
}

function renderInbox(tweets) {
  if (!tweets.length) {
    $("inbox").innerHTML = `<div class="empty">No hay publicaciones con este filtro.
      Añade cuentas y pulsa «Buscar ahora».</div>`;
    return;
  }
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
      toast("Buscando publicaciones nuevas. Puede tardar.");
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
      $("btn-poll").innerHTML = `${spinner("buscando")} ${progress.done || 0}/${progress.total || "?"}`;
      continue;
    }
    if (sawActivity || !poller.busy) {
      const last = poller.last_run;
      if (last && typeof last.new === "number") {
        toast(`Búsqueda terminada: ${last.new} publicación(es) nueva(s).`);
      } else if (last && last.skipped) {
        toast(`Búsqueda omitida: ${last.skipped}.`);
      }
      if (poller.last_error) toast(poller.last_error, "error");
      renderCounts(state.counts);
      return;
    }
  }
  toast("La búsqueda sigue en marcha; revisa en un momento.", "error");
}

/** Marca o desmarca una publicación como lista. */
async function markReady(tweetId, ready, button) {
  await withBusy(button, ready ? "Marcando…" : "Quitando…", async () => {
    try {
      await api(`/api/tweets/${tweetId}/ready`, {
        method: "POST",
        body: JSON.stringify({ ready }),
      });
      await loadTweets();
      await loadState();
      toast(
        ready
          ? "Marcada como lista. La limpieza automática no la borrará."
          : "Desmarcada: vuelve a entrar en la limpieza."
      );
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function copyLink(url, button) {
  if (!url) return toast("Esta publicación no tiene enlace.", "error");
  await withBusy(button, "Copiando…", async () => {
    const ok = await copyToClipboard(url);
    if (ok) toast("Enlace copiado.");
  });
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
      toast(result.purged ? "Limpieza completada." : "No había nada que limpiar.");
      await loadTweets();
      await loadState();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

/* ------------------------------------------------------------------ */
/* Pestañas                                                            */
/* ------------------------------------------------------------------ */
const TABS = ["inbox", "accounts", "settings"];

function tabFromHash() {
  const name = String(window.location.hash || "").replace("#", "").trim();
  return TABS.includes(name) ? name : "inbox";
}

function switchTab(name, updateHash = true) {
  const destino = TABS.includes(name) ? name : "inbox";
  for (const button of document.querySelectorAll("nav.tabs button")) {
    const activo = button.dataset.tab === destino;
    button.classList.toggle("active", activo);
    if (activo) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  for (const section of document.querySelectorAll("main > section")) {
    section.hidden = section.id !== `tab-${destino}`;
  }
  if (updateHash && window.location.hash !== `#${destino}`) {
    try {
      window.history.replaceState(null, "", `#${destino}`);
    } catch (error) {
      window.location.hash = destino;
    }
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
  if (target.id === "btn-purge") return purge();
  if (dataset.copy !== undefined) return copyLink(dataset.copy, target);
  if (dataset.ready) return markReady(dataset.ready, dataset.value === "1", target);

  if (dataset.toggle) {
    await api(`/api/accounts/${encodeURIComponent(dataset.toggle)}/active`, {
      method: "POST",
      body: JSON.stringify({ active: dataset.active !== "1" }),
    });
    await loadState();
    return;
  }
  if (dataset.remove) {
    await api(`/api/accounts/${encodeURIComponent(dataset.remove)}`, { method: "DELETE" });
    await loadState();
    await loadTweets();
  }
});

document.addEventListener("change", (event) => {
  const id = event.target.id;
  if (["filter-view", "filter-account", "filter-limit"].includes(id)) {
    loadTweets().catch((error) => toast(error.message, "error"));
  }
});

$("form-account").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("account-handle");
  const handle = input.value.trim();
  if (!handle) return;
  try {
    await api("/api/accounts", { method: "POST", body: JSON.stringify({ handle }) });
    input.value = "";
    toast("Cuenta añadida.");
    await loadState();
  } catch (error) {
    toast(error.message, "error");
  }
});

/* ------------------------------------------------------------------ */
/* Arranque                                                            */
/* ------------------------------------------------------------------ */
switchTab(tabFromHash(), false);
loadState()
  .then(loadTweets)
  .catch((error) => toast(error.message, "error"));

// Las horas relativas avanzan solas.
setInterval(tickRelativeTimes, 20000);
// Y los contadores se refrescan de vez en cuando, sin recargar la lista.
setInterval(() => {
  api("/api/state").then((state) => {
    renderCounts(state.counts);
    renderPollerState(state);
    rememberServerClock(state.clock);
  }).catch(() => {});
}, 60000);
