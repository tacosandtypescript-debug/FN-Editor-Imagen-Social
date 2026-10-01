/* Editor independiente de tarjeta — JavaScript puro, sin dependencias.
 *
 * La única operación con IA aquí es «Regenerar texto y caption», que vuelve a
 * generar el titular de arriba, el texto de abajo y el caption. Las imágenes y
 * el resto de ajustes no se tocan.
 */
"use strict";

const $ = (id) => document.getElementById(id);

const editor = {
  cardId: null,
  card: null,
  tweet: null,
  verifiedNowMs: null,
  clientAtMs: null,
};

/** Hora verificada del servidor avanzada con el reloj del navegador. */
function verifiedNowMs() {
  if (editor.verifiedNowMs === null) return Date.now();
  return editor.verifiedNowMs + (Date.now() - editor.clientAtMs);
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

/* ------------------------------------------------------------------ */
/* Utilidades                                                          */
/* ------------------------------------------------------------------ */
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let payload = null;
  try { payload = await response.json(); } catch (error) { payload = null; }
  if (!response.ok) {
    throw new Error((payload && payload.error) || `HTTP ${response.status}`);
  }
  return payload;
}

function toast(message, kind = "ok") {
  const node = document.createElement("div");
  node.className = kind;
  node.textContent = message;
  $("toast").appendChild(node);
  setTimeout(() => node.remove(), kind === "error" ? 9000 : 4200);
}

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function withBusy(button, label, task) {
  const original = button ? button.textContent : null;
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span> ${escapeHtml(label)}`;
  }
  return Promise.resolve().then(task).finally(() => {
    if (button) { button.disabled = false; button.textContent = original; }
  });
}

function fail(message) {
  $("error-box").hidden = false;
  $("editor-grid").hidden = true;
  $("error-text").textContent = message;
}

/* ------------------------------------------------------------------ */
/* Carga                                                               */
/* ------------------------------------------------------------------ */
function cardIdFromUrl() {
  const raw = new URLSearchParams(window.location.search).get("card");
  const parsed = parseInt(raw || "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

async function loadClock() {
  try {
    const state = await api("/api/state");
    const clock = state.clock || {};
    if (clock.now_utc) {
      const parsed = new Date(clock.now_utc);
      if (!Number.isNaN(parsed.getTime())) {
        editor.verifiedNowMs = parsed.getTime();
        editor.clientAtMs = Date.now();
      }
    }
    const skewed = Math.abs(clock.offset_seconds || 0) > 60;
    const pill = $("clock-pill");
    pill.className = `pill ${skewed ? "warn" : "ok"}`;
    pill.textContent = skewed
      ? `hora verificada · ${clock.local_offset_human || ""} (reloj del sistema ${clock.offset_human || "desviado"})`
      : `hora verificada · ${clock.local_offset_human || ""}`;
    pill.title = skewed
      ? "Las horas se calculan contra servidores públicos, no contra el reloj del sistema."
      : "El reloj del sistema coincide con la hora de referencia.";

    const select = $("f-analysis");
    select.innerHTML = (state.analysis_providers || []).map((provider) => (
      `<option value="${escapeHtml(provider.name)}" ${provider.available ? "" : "disabled"}>` +
      `${escapeHtml(provider.name)}${provider.available ? "" : " (no disponible)"}</option>`
    )).join("");
    if (state.settings && state.settings.analysis_provider) {
      select.value = state.settings.analysis_provider;
    }
  } catch (error) {
    $("clock-pill").className = "pill off";
    $("clock-pill").textContent = "sin datos de hora";
  }
}

function fillStyles() {
  const styles = ["auto", "adaptive", "grid", "bento", "mosaico", "puzzle", "jerarquico", "asimetrico"];
  $("f-style").innerHTML = styles.map((s) => `<option value="${s}">${s}</option>`).join("");
}

function applyParams(params) {
  params = params || {};
  $("f-top").value = params.top || "";
  $("f-bottom").value = params.bottom || "";
  $("f-caption").value = params.caption || "";
  if (params.format) $("f-format").value = params.format;
  if (params.fit) $("f-fit").value = params.fit;
  if (params.style) $("f-style").value = params.style;
  if (params.resolution) $("f-resolution").value = params.resolution;
  if (params.backend) $("f-backend").value = params.backend;
}

function currentParams() {
  return {
    top: $("f-top").value,
    bottom: $("f-bottom").value,
    caption: $("f-caption").value,
    format: $("f-format").value,
    fit: $("f-fit").value,
    style: $("f-style").value,
    resolution: $("f-resolution").value,
    backend: $("f-backend").value,
  };
}

function showCard(card) {
  editor.card = card;
  const meta = card.meta || {};
  const stamp = encodeURIComponent(card.output_path || card.id);
  const src = `/api/cards/${card.id}/image?v=${stamp}`;
  $("preview").innerHTML = `<img src="${src}" alt="Tarjeta">`;
  $("preview-meta").textContent =
    `v${card.version} · ${meta.width || "?"}×${meta.height || "?"} · ` +
    `${meta.output_format || ""} · ${meta.style || ""} · ${meta.resolution || ""} · ` +
    `${meta.images || 0} imagen(es)`;
  const link = $("download-link");
  link.href = src;
  link.download = `tarjeta-${card.tweet_id}-v${card.version}.png`;
  $("card-id").textContent = `tarjeta ${card.id} · v${card.version} · publicación ${card.tweet_id}`;
}

async function load() {
  const cardId = cardIdFromUrl();
  if (!cardId) {
    fail("Falta el identificador de la tarjeta. Abre el editor desde el botón «Abrir editor» de la bandeja.");
    return;
  }
  editor.cardId = cardId;
  try {
    const card = (await api(`/api/cards/${cardId}`)).card;
    editor.card = card;
    applyParams(card.params);
    showCard(card);
    $("editor-grid").hidden = false;

    try {
      const payload = await api(`/api/tweets/${card.tweet_id}`);
      editor.tweet = payload.tweet;
      renderTweet(payload.tweet);
    } catch (error) {
      $("tweet-info").innerHTML = `<span class="muted">No se pudo cargar la publicación: ${escapeHtml(error.message)}</span>`;
    }
    await loadDeliveries();
  } catch (error) {
    fail(error.message);
  }
}

function renderTweet(tweet) {
  // Miniaturas reducidas: pesan siete veces menos que el archivo original.
  const sources = tweet.thumbs && tweet.thumbs.length ? tweet.thumbs : (tweet.media || []);
  const media = sources.slice(0, 6).map((url) => (
    `<img src="${escapeHtml(url)}" alt="" loading="lazy" referrerpolicy="no-referrer"
          onerror="this.style.display='none'">`
  )).join("");
  $("tweet-info").innerHTML = `
    <div class="row small" style="margin-bottom:6px">
      <span class="account">@${escapeHtml(tweet.author_handle || tweet.source_handle)}</span>
      <strong data-posted="${escapeHtml(tweet.posted_at || tweet.fetched_at || "")}">${escapeHtml(tweet.posted_relative || "")}</strong>
      <span class="muted">· ${escapeHtml(tweet.posted_absolute || "")}</span>
    </div>
    <div style="white-space:pre-wrap">${escapeHtml(tweet.text || "(sin texto)")}</div>
    ${media ? `<div class="thumbs" style="margin-top:8px">${media}</div>` : ""}
    ${tweet.url ? `<p style="margin:8px 0 0"><a href="${escapeHtml(tweet.url)}" target="_blank" rel="noopener">Ver publicación original</a></p>` : ""}
  `;
}

async function loadDeliveries() {
  try {
    const payload = await api(`/api/cards/${editor.cardId}/deliveries`);
    const list = payload.deliveries || [];
    $("deliveries").innerHTML = list.length
      ? list.map((item) => (
          `<div style="border-bottom:1px solid #241b40;padding:5px 0">
             <span class="pill ${item.status === "ok" ? "ok" : "off"}">${escapeHtml(item.status)}</span>
             ${escapeHtml(item.target)} · ${escapeHtml(item.created_at)}
           </div>`
        )).join("")
      : `<span class="muted">Sin entregas todavía.</span>`;
  } catch (error) {
    $("deliveries").innerHTML = `<span class="muted">No se pudo leer el historial.</span>`;
  }
}

/* ------------------------------------------------------------------ */
/* Acciones                                                            */
/* ------------------------------------------------------------------ */
async function regenerateText() {
  const button = $("btn-regenerate");
  await withBusy(button, "Regenerando…", async () => {
    try {
      const payload = await api(`/api/cards/${editor.cardId}/regenerate-text`, {
        method: "POST",
        body: JSON.stringify({
          instructions: $("f-instructions").value,
          provider: $("f-analysis").value || null,
          render: true,
        }),
      });
      applyParams(payload.card.params);
      showCard(payload.card);
      const analysis = payload.analysis || {};
      $("regen-info").innerHTML =
        `<span class="pill ok">${escapeHtml(analysis.provider || "")}</span> ` +
        `<span class="muted">${escapeHtml(analysis.reasoning || "")}</span>` +
        `<div class="muted">Hashtags: ${escapeHtml((analysis.hashtags || []).join(" "))}</div>`;
      toast("Texto y caption regenerados; la tarjeta se ha recompuesto.");
    } catch (error) {
      $("regen-info").innerHTML = `<span class="pill off">error</span> ${escapeHtml(error.message)}`;
      toast(error.message, "error");
    }
  });
}

async function recompose() {
  const button = $("btn-render");
  await withBusy(button, "Recomponiendo…", async () => {
    try {
      const payload = await api(`/api/cards/${editor.cardId}/render`, {
        method: "POST",
        body: JSON.stringify({ params: currentParams() }),
      });
      applyParams(payload.card.params);
      showCard(payload.card);
      $("render-info").textContent = `v${payload.card.version} · ${payload.card.meta.width}×${payload.card.meta.height}`;
      toast("Tarjeta recompuesta.");
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function send() {
  const button = $("btn-send");
  await withBusy(button, "Enviando…", async () => {
    try {
      const payload = await api(`/api/cards/${editor.cardId}/send`, {
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
      await loadDeliveries();
    } catch (error) {
      $("send-result").innerHTML = `<span class="pill off">error</span> ${escapeHtml(error.message)}`;
      toast(error.message, "error");
    }
  });
}

/* ------------------------------------------------------------------ */
/* Arranque                                                            */
/* ------------------------------------------------------------------ */
$("btn-regenerate").addEventListener("click", regenerateText);
$("btn-render").addEventListener("click", recompose);
$("btn-send").addEventListener("click", send);
$("btn-back").addEventListener("click", () => window.close());

fillStyles();
loadClock();
load();

// Las horas relativas avanzan solas, igual que en la bandeja.
setInterval(tickRelativeTimes, 20000);
