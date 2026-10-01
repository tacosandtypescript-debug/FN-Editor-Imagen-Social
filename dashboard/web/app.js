/* EditImg Dashboard — interfaz en JavaScript puro, sin dependencias. */
"use strict";

const $ = (id) => document.getElementById(id);

const app = {
  state: null,
  tweets: [],
  selectedId: null,
  card: null,
  busy: false,
  //: Día actual en la hora local del usuario, según el reloj verificado.
  today: null,
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

function withBusy(button, label, task) {
  const original = button ? button.textContent : null;
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span> ${escapeHtml(label)}`;
  }
  return Promise.resolve()
    .then(task)
    .finally(() => {
      if (button) {
        button.disabled = false;
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
  renderProviderPills(state);
  renderCounts(state.counts);
  renderAccounts(state.accounts);
  renderProviders(state);
  renderSettings(state.settings);
  renderAnalysisStatus(state);
  renderMaintenance(state);
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
      `<span class="dot"></span>🕒 ${escapeHtml(clock.local_offset_human || "UTC")}` +
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
  const thumbs = (tweet.media || []).slice(0, 6).map((url) => (
    `<img src="${escapeHtml(url)}" alt="" loading="lazy" referrerpolicy="no-referrer"
          onerror="this.style.display='none'">`
  )).join("");
  const noMedia = !(tweet.media || []).length;
  // Botón «Procesar» siempre; «Abrir editor» solo cuando ya hay tarjeta.
  const editorButton = tweet.has_card
    ? `<button class="small accent" data-editor="${escapeHtml(String(tweet.card_id))}">Abrir editor</button>`
    : "";
  const processed = tweet.is_processed
    ? `<span class="done-flag">✓ procesada</span>`
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
        <strong>${escapeHtml(tweet.posted_relative || "sin fecha")}</strong>
        <span class="muted">· ${escapeHtml(tweet.posted_absolute || "")}</span>
        ${tweet.date_is_estimated ? `<span class="muted" title="La fuente no dio la fecha exacta; se usa la de descarga.">(aprox.)</span>` : ""}
      </div>
      <div class="text">${escapeHtml(tweet.text || "(sin texto)")}</div>
      ${noMedia
        ? `<div class="small" style="color:var(--warn)">Sin imágenes: el compositor necesita al menos una para crear la tarjeta.</div>`
        : `<div class="thumbs">${thumbs}</div>`}
      <div class="actions">
        <button class="small primary" data-process="${escapeHtml(tweet.tweet_id)}"
                ${noMedia ? "disabled title='Sin imágenes'" : ""}>${tweet.is_processed ? "Reprocesar" : "Procesar"}</button>
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
/* Procesar: analiza, descarga y compone, y revela «Abrir editor»      */
/* ------------------------------------------------------------------ */
async function processTweet(tweetId, button) {
  await withBusy(button, "Procesando…", async () => {
    try {
      const payload = await api(`/api/tweets/${tweetId}/process`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      const card = payload.card || {};
      toast(
        `Tarjeta v${card.version} lista (${(payload.steps || []).join(" + ")}). ` +
        "Ya puedes abrir el editor."
      );
      await loadTweets();
      await loadState();
    } catch (error) {
      toast(error.message, "error");
    }
  });
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
      ${(tweet.media || []).slice(0, 8).map((url) => (
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
  const src = `/api/cards/${card.id}/image?v=${encodeURIComponent(card.output_path || card.id)}`;
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
        `<span class="spinner"></span> ${progress.done || 0}/${progress.total || "?"}`;
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
        `<div class="muted" style="margin-top:6px">${escapeHtml(analysis.reasoning || "")}</div>`;
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
function switchTab(name) {
  for (const button of document.querySelectorAll("nav.tabs button")) {
    button.classList.toggle("active", button.dataset.tab === name);
  }
  for (const section of document.querySelectorAll("main > section")) {
    section.hidden = section.id !== `tab-${name}`;
  }
}

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
    if (app.selectedId) await openTweet(app.selectedId);
  } catch (error) {
    toast(error.message, "error");
  }
}

refreshAll();
setInterval(() => {
  api("/api/state").then((state) => {
    renderCounts(state.counts);
    renderEvents(state.events);
  }).catch(() => {});
}, 15000);
