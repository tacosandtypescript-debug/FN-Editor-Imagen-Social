const $ = (id) => document.getElementById(id);

const app = {
  state: null,
  counts: {},
  tweets: [],
  offset: 0,
  hasMore: false,
  telegramReady: false,
  verifiedNowMs: null,
  clientAtMs: Date.now(),
  listRequest: 0,
  pollWatch: null,
  confirmResolve: null,
  mediaFilter: "all",
  mediaCounts: {},
};

const TABS = ["inbox", "accounts", "system"];
const MEDIA_FILTERS = ["all", "images", "videos"];

async function api(path, options = {}) {
  const request = { ...options, headers: { ...(options.headers || {}) } };
  if (request.body && typeof request.body !== "string") {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(path, request);
  let payload = {};
  try { payload = await response.json(); } catch (error) { /* respuesta sin JSON */ }
  if (!response.ok) {
    throw new Error(payload.error || `La petición falló (${response.status}).`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function spinner() {
  return '<span class="spinner" aria-hidden="true"></span>';
}

function toast(message, kind = "ok") {
  const box = $("toast");
  if (!box) return;
  const node = document.createElement("div");
  node.className = kind;
  node.textContent = message;
  box.appendChild(node);
  window.setTimeout(() => node.remove(), kind === "error" ? 9000 : 4200);
}

function showCopyFallback(url) {
  const box = $("toast");
  if (!box) return;
  const node = document.createElement("div");
  node.className = "warn toast-copy";
  node.innerHTML =
    `<strong>No pude copiar automáticamente.</strong>` +
    `<span class="small">Selecciona el enlace o pulsa copiar otra vez.</span>` +
    `<input aria-label="Enlace de la publicación" readonly value="${escapeHtml(url)}">` +
    `<button class="button small secondary" type="button" data-fallback-copy>Copiar enlace</button>`;
  box.appendChild(node);
  const input = node.querySelector("input");
  if (input) { input.focus(); input.select(); }
  window.setTimeout(() => node.remove(), 12000);
}

function formatDate(value) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("es-ES", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "—";
  const total = Math.max(0, Math.round(Number(seconds)));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

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

function humanizeSince(isoDate, nowMs = verifiedNowMs()) {
  if (!isoDate) return "";
  const then = new Date(isoDate).getTime();
  if (Number.isNaN(then)) return "";
  let seconds = Math.floor((nowMs - then) / 1000);
  if (seconds < 0) seconds = 0;
  if (seconds < 60) return "hace unos segundos";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours < 24) return rest ? `hace ${hours} h ${rest} min` : `hace ${hours} h`;
  const days = Math.floor(seconds / 86400);
  if (days === 1) return "hace 1 día";
  if (days < 30) return `hace ${days} días`;
  const months = Math.floor(days / 30);
  if (months < 12) return months === 1 ? "hace 1 mes" : `hace ${months} meses`;
  const years = Math.floor(days / 365);
  return years <= 1 ? "hace 1 año" : `hace ${years} años`;
}

function tickRelativeTimes() {
  const now = verifiedNowMs();
  for (const node of document.querySelectorAll("[data-posted]")) {
    const label = humanizeSince(node.dataset.posted, now);
    if (label) node.textContent = label;
  }
}

function withBusy(button, label, task) {
  const original = button ? button.innerHTML : "";
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.innerHTML = `${spinner()}<span>${escapeHtml(label)}</span>`;
  }
  return Promise.resolve().then(task).finally(() => {
    if (button) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.innerHTML = original;
    }
  });
}

async function copyToClipboard(value) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch (error) { /* continúa con el respaldo */ }
  }
  try {
    const field = document.createElement("textarea");
    field.value = value;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.top = "-1000px";
    document.body.appendChild(field);
    field.select();
    field.setSelectionRange(0, value.length);
    const copied = document.execCommand("copy");
    field.remove();
    return copied;
  } catch (error) {
    return false;
  }
}

function setStatusChip(kind, label) {
  const chip = $("top-status");
  if (!chip) return;
  chip.className = `status-chip ${kind || ""}`.trim();
  chip.innerHTML = `<span class="status-dot ${kind || ""}" aria-hidden="true"></span><span>${escapeHtml(label)}</span>`;
}

function setMonitorState(kind, title, detail) {
  const box = $("monitor-state");
  if (!box) return;
  box.className = `monitor-state ${kind || ""}`.trim();
  box.innerHTML =
    `<span class="status-dot ${kind || ""}" aria-hidden="true"></span>` +
    `<div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div>`;
}

function renderCounts(counts) {
  app.counts = counts || {};
  const pending = Number(app.counts.nuevo || 0);
  const ready = Number(app.counts.listo || 0);
  const failed = Number(app.counts.fallido || 0);
  const metric = $("metric-new");
  if (metric) metric.textContent = String(pending);
  const summary = $("counts");
  if (summary) {
    const pieces = [`${pending} nuevas`, `${ready} conservadas`];
    if (failed) pieces.push(`${failed} con error`);
    summary.innerHTML = pieces.map((part, index) =>
      `${index ? '<span class="separator">·</span>' : ""}<span>${escapeHtml(part)}</span>`
    ).join("");
  }
}

function mediaFilterLabel(value) {
  return ({ all: "Todas", images: "Imágenes", videos: "Vídeos" })[value] || "Todas";
}

function renderMediaTabs(mediaCounts = app.mediaCounts) {
  app.mediaCounts = mediaCounts || {};
  for (const button of document.querySelectorAll("#media-tabs [data-media-filter]")) {
    const active = button.dataset.mediaFilter === app.mediaFilter;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
    const count = button.querySelector(".media-tab-count");
    if (count) count.textContent = Number.isFinite(Number(app.mediaCounts[button.dataset.mediaFilter]))
      ? String(app.mediaCounts[button.dataset.mediaFilter])
      : "—";
  }
  const panel = $("inbox");
  const activeTab = $(`media-tab-${app.mediaFilter}`);
  if (panel && activeTab) panel.setAttribute("aria-labelledby", activeTab.id);
  const note = $("media-filter-note");
  if (note) {
    const notes = {
      all: "Todas las publicaciones detectadas.",
      images: "Solo publicaciones con imágenes, sin vídeo.",
      videos: "Publicaciones que contienen vídeo, tengan o no miniatura.",
    };
    note.textContent = notes[app.mediaFilter] || notes.all;
  }
}

function normaliseMediaFilter(value) {
  const candidate = String(value || "").toLowerCase();
  return MEDIA_FILTERS.includes(candidate) ? candidate : "all";
}

async function setMediaFilter(value) {
  const next = normaliseMediaFilter(value);
  if (next === app.mediaFilter) {
    renderMediaTabs();
    return;
  }
  app.mediaFilter = next;
  renderMediaTabs();
  await refreshList();
}

function activateMediaFilter(value) {
  setMediaFilter(value).catch((error) => toast(error.message, "error"));
}

function renderMonitor(state) {
  const poller = state.poller || {};
  const accounts = state.accounts || [];
  const active = accounts.filter((account) => account.active).length;
  const providerReady = (state.timeline_providers || []).some((provider) => provider.available);
  const last = poller.last_run_at ? formatDate(poller.last_run_at) : "todavía no hay sondeo";
  const next = !poller.periodic ? "manual" : poller.busy ? "en curso" : formatDuration(poller.next_run_in_seconds);
  const accountMetric = $("metric-accounts");
  if (accountMetric) accountMetric.textContent = String(active);
  const accountNote = $("metric-accounts-note");
  if (accountNote) accountNote.textContent = active ? "revisión automática activa" : "añade una cuenta";
  const nextMetric = $("metric-next");
  if (nextMetric) nextMetric.textContent = next;
  const lastMetric = $("metric-last");
  if (lastMetric) lastMetric.textContent = poller.busy ? "revisando ahora" : `última: ${last}`;

  let kind = "ok";
  let title = "Monitor activo";
  let detail = active ? `${active} cuenta(s) · ${providerReady ? "fuente disponible" : "fuente pendiente"}` : "Añade una cuenta para empezar";
  if (!active) { kind = "warn"; title = "Monitor en espera"; }
  else if (!poller.periodic) { kind = "warn"; title = "Automático detenido"; detail = "El botón manual sigue disponible"; }
  else if (poller.busy) { kind = "warn"; title = "Sondeo en curso"; detail = poller.progress && poller.progress.current ? `leyendo @${poller.progress.current}` : "consultando fuentes"; }
  else if (poller.last_error) { kind = "off"; title = "Monitor con error"; detail = poller.last_error; }
  setStatusChip(kind, title);
  setMonitorState(kind, title, detail);
  const summary = $("monitor-summary");
  if (summary) summary.textContent = poller.periodic ? `Próxima revisión: ${next} · última: ${last}` : "La revisión automática está desactivada en la configuración local.";
  const health = $("system-health");
  if (health) { health.className = `health-stamp ${kind}`; health.textContent = title; }
}

function renderPollerState(state) {
  const box = $("poller-state");
  if (!box) return;
  const poller = state.poller || {};
  const progress = poller.progress || {};
  if (!poller.periodic) {
    box.innerHTML = `<span class="state-label warn"><span class="status-dot warn"></span>Revisión automática desactivada</span><span>El botón “Buscar ahora” sigue funcionando.</span>`;
  } else if (poller.busy) {
    const current = progress.current ? ` · leyendo @${escapeHtml(progress.current)}` : "";
    box.innerHTML = `<span class="state-label warn">${spinner()} Buscando publicaciones</span><span>${escapeHtml(String(progress.done || 0))}/${escapeHtml(String(progress.total || "?"))}${current}</span>`;
  } else if (poller.last_error) {
    box.innerHTML = `<span class="state-label off"><span class="status-dot off"></span>Último sondeo con error</span><span>${escapeHtml(poller.last_error)}</span>`;
  } else {
    box.innerHTML = `<span class="state-label ok"><span class="status-dot ok"></span>Monitor activo</span><span>cada ${escapeHtml(formatDuration(poller.interval_seconds))} · próxima en ${escapeHtml(formatDuration(poller.next_run_in_seconds))}</span>`;
  }
}

function renderTelegram(state) {
  const telegram = state.telegram || {};
  app.telegramReady = Boolean(telegram.ready);
  const configured = Boolean(telegram.configured);
  const metric = $("metric-telegram");
  const note = $("metric-telegram-note");
  const status = $("telegram-status");
  if (app.telegramReady) {
    if (metric) metric.textContent = "Listo";
    if (note) note.textContent = "puedes entregar enlaces";
    if (status) status.innerHTML = `<span class="status-dot ok"></span><div><strong>Telegram conectado</strong><span>La entrega está disponible.</span></div>`;
    if (status) status.className = "integration-status ready";
    return;
  }
  if (metric) metric.textContent = configured ? "Pendiente" : "Sin conectar";
  if (note) note.textContent = configured ? "falta el adaptador" : "configurar en Sistema";
  if (status) {
    status.innerHTML = `<span class="status-dot warn"></span><div><strong>${configured ? "Configuración detectada" : "Telegram aún no está conectado"}</strong><span>${configured ? "El bot y el chat están definidos; falta conectar el flujo de entrega." : "Las publicaciones se pueden abrir y copiar mientras terminamos esta conexión."}</span></div>`;
    status.className = "integration-status pending";
  }
}

function fillAccountFilter(accounts) {
  const select = $("filter-account");
  if (!select) return;
  const previous = select.value;
  select.innerHTML = `<option value="">Todas las cuentas</option>` + (accounts || [])
    .map((account) => `<option value="${escapeHtml(account.handle)}">@${escapeHtml(account.handle)}</option>`)
    .join("");
  select.value = (accounts || []).some((account) => account.handle === previous) ? previous : "";
}

function renderAccounts(accounts) {
  const list = accounts || [];
  const active = list.filter((account) => account.active).length;
  const total = $("account-total");
  const activeCount = $("account-active-count");
  if (total) total.textContent = String(list.length);
  if (activeCount) activeCount.textContent = `${active} activa${active === 1 ? "" : "s"}`;
  const box = $("accounts");
  if (!box) return;
  if (!list.length) {
    box.innerHTML = `<div class="empty-state"><h3>Aún no hay cuentas</h3><p>Añade la primera fuente arriba y el monitor empezará a tener algo que revisar.</p></div>`;
    return;
  }
  box.innerHTML = list.map((account) => {
    const activeClass = account.active ? "active" : "paused";
    const status = account.active ? "activa" : "pausada";
    const detail = account.last_error
      ? `<div class="account-detail error">${escapeHtml(account.last_error)}</div>`
      : `<div class="account-detail">Última revisión: ${escapeHtml(account.last_checked_at ? formatDate(account.last_checked_at) : "nunca")}</div>`;
    return `<div class="account-row">
      <div class="account-main">
        <div class="account-name">@${escapeHtml(account.handle)}<span class="account-state ${activeClass}"><span class="status-dot ${account.active ? "ok" : "warn"}"></span>${status}</span></div>
        ${detail}
      </div>
      <div class="account-actions">
        <button class="button small secondary" type="button" data-toggle="${escapeHtml(account.handle)}" data-active="${account.active ? "1" : "0"}" aria-pressed="${account.active ? "true" : "false"}">${account.active ? "Pausar" : "Activar"}</button>
        <button class="button small danger" type="button" data-remove="${escapeHtml(account.handle)}">Eliminar</button>
      </div>
    </div>`;
  }).join("");
}

function renderProviders(state) {
  const box = $("providers");
  if (!box) return;
  const providers = state.timeline_providers || [];
  const rows = providers.map((provider) =>
    `<div class="provider-row"><span class="name">${escapeHtml(provider.name)}</span><span class="detail"><span class="provider-token ${provider.available ? "ok" : "warn"}">${provider.available ? "disponible" : "pendiente"}</span> ${escapeHtml(provider.detail || "")}</span></div>`
  );
  if (state.profiles && state.profiles.x) rows.push(`<div class="provider-row"><span class="name">Perfil de navegador</span><span class="detail">${escapeHtml(state.profiles.x)}</span></div>`);
  box.innerHTML = rows.length ? rows.join("") : `<div class="empty-state"><p>No hay proveedores reportados.</p></div>`;
}

function renderSettings(settings) {
  const box = $("settings");
  if (!box || !settings) return;
  const rows = [
    ["Fuente principal", settings.timeline_provider],
    ["Intervalo", formatDuration(settings.poll_interval_seconds)],
    ["Sondeo al arrancar", settings.poll_on_start ? "sí" : "no"],
    ["Retención", `${settings.retention_hours} h`],
    ["Navegador", `${settings.browser_channel} · visible: ${settings.browser_headless ? "no" : "sí"}`],
    ["Telegram", settings.telegram_bot_token_set && settings.telegram_chat_id_set ? "configurado" : "sin configurar"],
  ];
  box.innerHTML = rows.map(([key, value]) => `<div class="setting-row"><span class="key">${escapeHtml(key)}</span><span class="value">${escapeHtml(String(value ?? "—"))}</span></div>`).join("");
}

function renderMaintenance(state) {
  const box = $("maintenance-state");
  if (!box) return;
  const info = state.maintenance || {};
  box.innerHTML =
    `<span class="maintenance-token">retención: ${escapeHtml(String(info.retention_hours ?? "—"))} h</span>` +
    `<span class="maintenance-token ok">${escapeHtml(String(info.total_seen || 0))} vistas recordadas</span>` +
    `<span class="maintenance-token ${info.purge_due ? "warn" : "ok"}">${info.purge_due ? "limpieza pendiente" : "al día"}</span>`;
}

function renderEvents(events) {
  const box = $("events");
  if (!box) return;
  const list = events || [];
  box.innerHTML = list.length
    ? list.map((event) => `<div class="${event.level === "error" ? "error" : ""}"><span>${escapeHtml(formatDate(event.ts))}</span> · ${escapeHtml(event.message)}</div>`).join("")
    : `<div>Sin actividad todavía.</div>`;
}

async function loadState() {
  const state = await api("/api/state");
  app.state = state;
  rememberServerClock(state.clock);
  renderCounts(state.counts);
  renderMonitor(state);
  renderPollerState(state);
  renderTelegram(state);
  renderAccounts(state.accounts);
  fillAccountFilter(state.accounts);
  renderProviders(state);
  renderSettings(state.settings);
  renderMaintenance(state);
  renderEvents(state.events);
  return state;
}

function sortKey(tweet) {
  return tweet.posted_at || tweet.fetched_at || "";
}

function shortToIso(shortDate) {
  const parts = String(shortDate || "").split("/");
  return parts.length === 3 ? `${parts[2]}-${parts[1]}-${parts[0]}` : "";
}

function shiftIsoDay(isoDate, delta) {
  const parts = String(isoDate || "").split("-").map(Number);
  if (parts.length !== 3 || parts.some((value) => !Number.isFinite(value))) return "";
  const moment = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  moment.setUTCDate(moment.getUTCDate() + delta);
  return moment.toISOString().slice(0, 10);
}

function dayLabel(shortDate) {
  const iso = shortToIso(shortDate);
  if (!iso) return "Sin fecha";
  const today = ((app.state && app.state.clock && app.state.clock.now_local) || "").slice(0, 10);
  if (today && iso === today) return "Hoy";
  if (today && iso === shiftIsoDay(today, -1)) return "Ayer";
  const parts = shortDate.split("/");
  const months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  return `${Number(parts[0])} ${months[Number(parts[1]) - 1] || parts[1]} ${parts[2]}`;
}

function tweetCard(tweet) {
  const list = tweet.thumbs && tweet.thumbs.length ? tweet.thumbs : (tweet.media || []);
  const media = list.slice(0, 6).map((url) =>
    `<a href="${escapeHtml(tweet.url || "#")}" target="_blank" rel="noopener" class="shot"><img src="${escapeHtml(url)}" alt="Miniatura de @${escapeHtml(tweet.author_handle || tweet.source_handle)}" loading="lazy" referrerpolicy="no-referrer">${tweet.has_video ? '<span class="video-flag">vídeo</span>' : ""}</a>`
  ).join("");
  const mediaKind = tweet.has_video ? "video" : tweet.has_media ? "image" : "text";
  const mediaLabel = tweet.has_video ? "Vídeo" : tweet.has_media ? "Imagen" : "Texto";
  const mediaPreview = media || (tweet.has_video
    ? `<div class="tweet-media-note video"><strong>Vídeo detectado</strong><span>La miniatura no está disponible; ábrelo en X para reproducirlo.</span></div>`
    : "");
  const ready = tweet.status === "listo";
  const duplicate = Boolean(tweet.is_duplicate);
  const statusClass = ready ? "ready" : duplicate ? "duplicate" : "";
  const statusLabel = ready ? "Conservada" : duplicate ? "Repetida" : "Nueva";
  const classes = `tweet${ready ? " ready" : ""}`;
  const id = escapeHtml(tweet.tweet_id);
  const url = escapeHtml(tweet.url || "");
  const telegramDisabled = !app.telegramReady || !tweet.url;
  const telegramReason = !app.telegramReady ? "Telegram se conectará en la Fase 2." : "Esta publicación no tiene enlace.";
  return `<article class="${classes}" data-tweet-id="${id}" data-media-kind="${mediaKind}">
    <div class="tweet-main">
      <div class="tweet-meta"><span class="tweet-status ${statusClass}">${statusLabel}</span><span class="media-kind ${mediaKind}">${mediaLabel}</span><span class="account">@${escapeHtml(tweet.author_handle || tweet.source_handle)}</span>${tweet.author_handle && tweet.author_handle !== tweet.source_handle ? `<span class="via">vía @${escapeHtml(tweet.source_handle)}</span>` : ""}${ready ? '<span class="done-flag">· protegida</span>' : ""}</div>
      <div class="tweet-when"><strong data-posted="${escapeHtml(tweet.posted_at || tweet.fetched_at || "")}">${escapeHtml(tweet.posted_relative || "sin fecha")}</strong><span>· ${escapeHtml(tweet.posted_absolute || "")}</span></div>
      <div class="tweet-text">${escapeHtml(tweet.text || "(sin texto)")}</div>
      ${mediaPreview ? (media ? `<div class="tweet-media">${media}</div>` : mediaPreview) : ""}
    </div>
    <div class="tweet-actions">
      ${tweet.url ? `<a class="button small primary" href="${url}" target="_blank" rel="noopener">Abrir en X</a>` : `<span class="button small secondary" aria-disabled="true">Sin enlace</span>`}
      <button class="button small secondary" type="button" data-copy="${url}" ${tweet.url ? "" : "disabled"}>Copiar enlace</button>
      <button class="button small telegram" type="button" data-telegram="${id}" ${telegramDisabled ? `disabled title="${escapeHtml(telegramReason)}"` : ""}>Enviar a Telegram</button>
      ${!app.telegramReady ? `<p class="action-note">${escapeHtml(telegramReason)}</p>` : ""}
      <button class="button small ghost" type="button" data-ready="${id}" data-value="${ready ? "0" : "1"}">${ready ? "Quitar de conservadas" : "Conservar publicación"}</button>
    </div>
  </article>`;
}

function renderLoading(append = false) {
  const box = $("inbox");
  if (!box) return;
  box.setAttribute("aria-busy", "true");
  if (!append) box.innerHTML = `<div class="loading-state"><h3>${spinner()}Buscando publicaciones</h3><p>Estamos leyendo el último estado del radar.</p></div>`;
}

function renderInboxError(error) {
  const box = $("inbox");
  if (!box) return;
  box.setAttribute("aria-busy", "false");
  box.innerHTML = `<div class="empty-state"><h3>No pude cargar la bandeja</h3><p>${escapeHtml(error.message || "Error desconocido")}</p><button class="button secondary" type="button" data-retry-list>Reintentar</button></div>`;
}

function renderInbox(tweets) {
  const box = $("inbox");
  if (!box) return;
  box.setAttribute("aria-busy", "false");
  if (!tweets.length) {
    const hasAccounts = (app.state && app.state.accounts || []).length > 0;
    const scope = app.mediaFilter === "all" ? "publicaciones" : mediaFilterLabel(app.mediaFilter).toLowerCase();
    box.innerHTML = `<div class="empty-state"><h3>${hasAccounts ? `No hay ${scope}` : "El radar está vacío"}</h3><p>${hasAccounts ? "No hay señales con este filtro. Puedes lanzar una revisión manual o volver más tarde." : "Añade una cuenta en Cuentas y pulsa “Buscar ahora” para empezar."}</p></div>`;
    return;
  }
  const ordered = [...tweets].sort((a, b) => sortKey(b).localeCompare(sortKey(a)));
  const blocks = [];
  let currentDay = null;
  let buffer = [];
  let count = 0;
  const flush = () => {
    if (!buffer.length) return;
    const publicationLabel = count === 1 ? "1 publicación" : `${count} publicaciones`;
    blocks.push(`<div class="feed-day"><span class="label">${escapeHtml(dayLabel(currentDay))}</span><span class="count">${publicationLabel}</span><span class="line"></span></div>${buffer.join("")}`);
    buffer = [];
    count = 0;
  };
  for (const tweet of ordered) {
    const day = tweet.posted_short || "";
    if (day !== currentDay) { flush(); currentDay = day; }
    buffer.push(tweetCard(tweet));
    count += 1;
  }
  flush();
  box.innerHTML = blocks.join("");
}

function renderPagination() {
  const panel = $("feed-pagination");
  if (!panel) return;
  panel.hidden = !app.hasMore;
  const summary = $("feed-summary");
  if (summary) summary.textContent = String(app.tweets.length);
}

async function loadTweets({ append = false } = {}) {
  const generation = ++app.listRequest;
  const view = $("filter-view").value;
  const handle = $("filter-account").value;
  const limit = Number($("filter-limit").value) || 48;
  const offset = append ? app.offset : 0;
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  query.set("media", app.mediaFilter);
  if (view === "listas") { query.set("status", "listo"); query.set("pending", "0"); }
  else if (view === "todas") query.set("pending", "0");
  else query.set("pending", "1");
  if (handle) query.set("handle", handle);
  if (!append) renderLoading();
  try {
    const payload = await api(`/api/tweets?${query.toString()}`);
    if (generation !== app.listRequest) return;
    const incoming = payload.tweets || [];
    app.tweets = append ? app.tweets.concat(incoming) : incoming;
    app.offset = Number(payload.next_offset ?? app.tweets.length);
    app.hasMore = Boolean(payload.has_more);
    renderCounts(payload.counts || app.counts);
    renderMediaTabs(payload.media_counts || app.mediaCounts);
    renderInbox(app.tweets);
    renderPagination();
  } catch (error) {
    if (!append) renderInboxError(error);
    else toast(error.message, "error");
    throw error;
  }
}

async function refreshList() {
  await loadTweets({ append: false });
}

async function loadMoreTweets() {
  const button = $("btn-load-more");
  await withBusy(button, "Cargando…", () => loadTweets({ append: true }));
}

async function startPoll() {
  const button = $("btn-poll");
  await withBusy(button, "Iniciando…", async () => {
    try {
      const response = await api("/api/poll", { method: "POST", body: { background: true } });
      if (!response.started) { toast("El sondeo no llegó a arrancar.", "error"); return; }
      toast("Sondeo iniciado. Puedes seguir usando el radar.");
      await loadState();
      watchPoller();
    } catch (error) { toast(error.message, "error"); }
  });
}

function watchPoller() {
  if (app.pollWatch) return;
  app.pollWatch = (async () => {
    for (let attempt = 0; attempt < 180; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      try {
        const state = await loadState();
        if (!state.poller || !state.poller.busy) {
          const last = state.poller && state.poller.last_run;
          if (last && typeof last.new === "number") toast(`Sondeo terminado: ${last.new} publicación(es) nueva(s).`);
          if (state.poller && state.poller.last_error) toast(state.poller.last_error, "error");
          await refreshList();
          return;
        }
      } catch (error) { /* el siguiente ciclo puede recuperar la conexión */ }
    }
    toast("El sondeo sigue en marcha; el estado queda visible arriba.", "warn");
  })().finally(() => { app.pollWatch = null; });
}

async function markReady(tweetId, ready, button) {
  await withBusy(button, ready ? "Conservando…" : "Quitando…", async () => {
    try {
      await api(`/api/tweets/${encodeURIComponent(tweetId)}/ready`, { method: "POST", body: { ready } });
      await loadState();
      await refreshList();
      toast(ready ? "Publicación conservada; no la borrará la limpieza." : "Publicación devuelta a la bandeja.");
    } catch (error) { toast(error.message, "error"); }
  });
}

async function copyLink(url, button) {
  if (!url) { toast("Esta publicación no tiene enlace.", "error"); return; }
  await withBusy(button, "Copiando…", async () => {
    const copied = await copyToClipboard(url);
    if (copied) toast("Enlace copiado.");
    else showCopyFallback(url);
  });
}

async function sendToTelegram(tweetId, button) {
  if (!app.telegramReady) { toast("Telegram todavía no está conectado.", "warn"); return; }
  await withBusy(button, "Enviando…", async () => {
    try {
      await api(`/api/tweets/${encodeURIComponent(tweetId)}/telegram`, { method: "POST", body: {} });
      toast("Enlace enviado a Telegram.");
    } catch (error) { toast(error.message, "error"); }
  });
}

async function purge(button) {
  await withBusy(button, "Limpiando…", async () => {
    try {
      const result = await api("/api/maintenance/purge", { method: "POST", body: { force: true } });
      $("purge-result").textContent = result.purged
        ? `Hecho: ${result.purged} publicación(es) limpiada(s); ${result.seen_kept || 0} vistas siguen recordadas.`
        : "No había publicaciones que limpiar.";
      toast(result.purged ? "Limpieza completada." : "No había nada que limpiar.");
      await loadState();
      await refreshList();
    } catch (error) { toast(error.message, "error"); }
  });
}

function askConfirmation(handle) {
  const dialog = $("confirm-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return Promise.resolve(false);
  $("confirm-title").textContent = `¿Eliminar @${handle}?`;
  $("confirm-message").textContent = "La cuenta dejará de vigilarse; las publicaciones ya guardadas no se borrarán automáticamente por esta acción.";
  dialog.showModal();
  return new Promise((resolve) => { app.confirmResolve = resolve; });
}

async function removeAccount(handle, button) {
  if (!(await askConfirmation(handle))) return;
  await withBusy(button, "Eliminando…", async () => {
    try {
      await api(`/api/accounts/${encodeURIComponent(handle)}`, { method: "DELETE" });
      toast(`@${handle} ya no se vigila.`);
      await loadState();
      await refreshList();
    } catch (error) { toast(error.message, "error"); }
  });
}

function tabFromHash() {
  const name = String(window.location.hash || "").replace(/^#/, "").trim();
  return TABS.includes(name) ? name : "inbox";
}

function switchTab(name, updateHash = true) {
  const destination = TABS.includes(name) ? name : "inbox";
  for (const button of document.querySelectorAll(".tabs [data-tab]")) {
    const active = button.dataset.tab === destination;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  }
  for (const section of document.querySelectorAll("main > section")) section.hidden = section.id !== `tab-${destination}`;
  const titles = { inbox: "Radar", accounts: "Cuentas", system: "Sistema" };
  document.title = `EditImg · ${titles[destination]}`;
  if (updateHash && window.location.hash !== `#${destination}`) {
    try { window.history.replaceState(null, "", `#${destination}`); }
    catch (error) { window.location.hash = destination; }
  }
}

window.addEventListener("hashchange", () => switchTab(tabFromHash(), false));

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, a");
  if (!target) return;
  const data = target.dataset || {};
  if (data.retryList !== undefined) { await refreshList(); return; }
  if (data.copy !== undefined) { await copyLink(data.copy, target); return; }
  if (data.fallbackCopy !== undefined) {
    const input = target.closest("div")?.querySelector("input");
    if (input && await copyToClipboard(input.value)) { toast("Enlace copiado."); target.closest("div").remove(); }
    return;
  }
  if (data.ready) { await markReady(data.ready, data.value === "1", target); return; }
  if (data.telegram) { await sendToTelegram(data.telegram, target); return; }
  if (data.toggle) {
    await withBusy(target, target.dataset.active === "1" ? "Pausando…" : "Activando…", async () => {
      try {
        await api(`/api/accounts/${encodeURIComponent(data.toggle)}/active`, { method: "POST", body: { active: data.active !== "1" } });
        await loadState();
        toast(data.active === "1" ? "Cuenta pausada." : "Cuenta activada.");
      } catch (error) { toast(error.message, "error"); }
    });
    return;
  }
  if (data.remove) { await removeAccount(data.remove, target); }
});

document.addEventListener("change", (event) => {
  if (["filter-view", "filter-account", "filter-limit"].includes(event.target.id)) {
    refreshList().catch((error) => toast(error.message, "error"));
  }
});

document.addEventListener("keydown", (event) => {
  const current = event.target.closest("#media-tabs [role=tab]");
  if (!current) return;
  const tabs = [...document.querySelectorAll("#media-tabs [role=tab]")];
  const index = tabs.indexOf(current);
  if (index < 0) return;
  let next = null;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") next = tabs[(index + 1) % tabs.length];
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = tabs[(index - 1 + tabs.length) % tabs.length];
  if (event.key === "Home") next = tabs[0];
  if (event.key === "End") next = tabs[tabs.length - 1];
  if (next) {
    event.preventDefault();
    next.focus();
    return;
  }
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    activateMediaFilter(current.dataset.mediaFilter);
  }
});

$("form-account").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("account-handle");
  const button = event.currentTarget.querySelector("button[type=submit]");
  const handle = input.value.trim();
  if (!handle) { input.focus(); toast("Escribe una cuenta de X.", "warn"); return; }
  await withBusy(button, "Añadiendo…", async () => {
    try {
      await api("/api/accounts", { method: "POST", body: { handle } });
      input.value = "";
      toast("Cuenta añadida al radar.");
      await loadState();
    } catch (error) { toast(error.message, "error"); }
  });
});

$("confirm-form").addEventListener("submit", (event) => {
  const dialog = $("confirm-dialog");
  const confirmed = event.submitter && event.submitter.value === "confirm";
  if (app.confirmResolve) app.confirmResolve(confirmed);
  app.confirmResolve = null;
  if (dialog.open) dialog.close(confirmed ? "confirm" : "cancel");
});

$("confirm-dialog").addEventListener("cancel", () => {
  if (app.confirmResolve) app.confirmResolve(false);
  app.confirmResolve = null;
});

switchTab(tabFromHash(), false);
renderMediaTabs();
renderLoading();
loadState()
  .then(() => loadTweets())
  .catch((error) => {
    renderInboxError(error);
    toast(error.message, "error");
  });

window.setInterval(tickRelativeTimes, 20000);
window.setInterval(() => {
  loadState().catch(() => {});
}, 30000);
