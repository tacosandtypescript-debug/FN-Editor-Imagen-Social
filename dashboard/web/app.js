/* EditImg Dashboard — interfaz en JavaScript puro, sin dependencias. */
"use strict";

const $ = (id) => document.getElementById(id);

const app = {
  state: null,
  tweets: [],
  selectedId: null,
  card: null,
  busy: false,
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
  renderProviderPills(state);
  renderCounts(state.counts);
  renderAccounts(state.accounts);
  renderProviders(state);
  renderSettings(state.settings);
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
  const query = new URLSearchParams({ status, limit });
  if (handle) query.set("handle", handle);
  const payload = await api(`/api/tweets?${query.toString()}`);
  app.tweets = payload.tweets || [];
  renderCounts(payload.counts);
  renderInbox(app.tweets);
}

function renderInbox(tweets) {
  if (!tweets.length) {
    $("inbox").innerHTML = `<div class="empty">No hay publicaciones con este filtro.
      Añade cuentas y pulsa «Buscar ahora».</div>`;
    return;
  }
  $("inbox").innerHTML = tweets.map((tweet) => {
    const thumbs = (tweet.media || []).slice(0, 6).map((url) => (
      `<img src="${escapeHtml(url)}" alt="" loading="lazy" referrerpolicy="no-referrer"
            onerror="this.style.display='none'">`
    )).join("");
    const noMedia = !(tweet.media || []).length;
    return `
      <div class="tweet ${app.selectedId === tweet.tweet_id ? "selected" : ""}">
        <div class="meta">
          <span class="badge ${escapeHtml(tweet.status)}">${escapeHtml(tweet.status)}</span>
          <span class="account">@${escapeHtml(tweet.author_handle || tweet.source_handle)}</span>
          ${tweet.author_handle && tweet.author_handle !== tweet.source_handle
            ? `<span class="muted">vía @${escapeHtml(tweet.source_handle)}</span>` : ""}
          <span>${escapeHtml(tweet.posted_at ? formatDate(tweet.posted_at) : (tweet.relative_time || "sin fecha"))}</span>
        </div>
        <div class="text">${escapeHtml(tweet.text || "(sin texto)")}</div>
        ${noMedia
          ? `<div class="small" style="color:var(--warn)">Sin imágenes: el compositor necesita al menos una para crear la tarjeta.</div>`
          : `<div class="thumbs">${thumbs}</div>`}
        <div class="actions">
          <button class="small primary" data-open="${escapeHtml(tweet.tweet_id)}">Abrir en el editor</button>
          <button class="small" data-analyze="${escapeHtml(tweet.tweet_id)}">Analizar</button>
          ${tweet.url ? `<a class="small" href="${escapeHtml(tweet.url)}" target="_blank" rel="noopener">Ver original</a>` : ""}
          <button class="small ghost" data-status="${escapeHtml(tweet.tweet_id)}" data-value="descartado">Descartar</button>
        </div>
      </div>`;
  }).join("");
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
      await api("/api/poll", { method: "POST", body: JSON.stringify({ background: true }) });
      toast("Sondeo iniciado en segundo plano; la bandeja se actualizará sola.");
      await waitForPoller();
      await loadTweets();
      await loadState();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function waitForPoller() {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const state = await api("/api/state");
    if (!state.poller.busy) {
      const last = state.poller.last_run;
      if (last) toast(`Sondeo terminado: ${last.new} publicación(es) nueva(s).`);
      if (state.poller.last_error) toast(state.poller.last_error, "error");
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
  if (target.id === "btn-analyze") return analyzeSelected();
  if (target.id === "btn-generate") return generateCard();
  if (target.id === "btn-render") return renderCard();
  if (target.id === "btn-send") return sendCard();

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

for (const id of ["filter-status", "filter-account", "filter-limit"]) {
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
