// Compagnon de révision — front Phase A, vanilla JS.
// Cf. ARCHITECTURE.md §8.3.

const QUOTA_POLL_MS = 60_000;

const $ = (sel) => document.querySelector(sel);
const dialogue = $("#dialogue-stream");
const userInput = $("#user-input");
const sendBtn = $("#send-btn");
const endBtn = $("#end-session");
const startForm = $("#start-form");
const sessionInfo = $("#session-info");
const quotaContent = $("#quota-content");
const recordIndicator = $("#record-indicator");

let activeSession = null;
let currentEventSource = null;
let currentClaudeTurn = null;

// ============================================================ Quota poll

async function refreshQuota() {
  try {
    const r = await fetch("/api/quota");
    const data = await r.json();
    quotaContent.innerHTML = renderQuota(data);
  } catch (e) {
    quotaContent.textContent = "(quota indisponible)";
  }
}

function renderQuota(d) {
  if (d.error) return `<div>(${d.error})</div>`;
  const row = (label, pct) => {
    if (pct == null) return "";
    const cls = pct >= 90 ? "err" : pct >= 70 ? "warn" : "";
    return `<div>${label}<br><span class="bar ${cls}"><span style="width:${Math.min(pct,100)}%"></span></span>${pct.toFixed(0)} %</div>`;
  };
  return [
    row("Session 5h", d.session_pct),
    row("Hebdo 7j", d.weekly_pct),
    row("Hebdo Sonnet", d.weekly_sonnet_pct),
    row("Overage", d.extra_pct),
  ].join("");
}

setInterval(refreshQuota, QUOTA_POLL_MS);
refreshQuota();

// ============================================================ Pré-remplissage du formulaire
// compagnon.py CLI passe les args via query params : ?matiere=AN1&type=TD&num=5&exo=3
(function prefillFromQueryParams() {
  const params = new URLSearchParams(window.location.search);
  for (const [k, v] of params) {
    const input = startForm.querySelector(`[name="${k}"]`);
    if (input) input.value = v;
  }
})();

// ============================================================ Start session

startForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(startForm);
  const body = Object.fromEntries(fd.entries());
  try {
    const r = await fetch("/api/start_session", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) { alert("Erreur: " + (data.error || r.status)); return; }
    activeSession = data.session_id;
    sessionInfo.textContent = `→ ${data.session_id} (engine: ${data.engine})`;
    dialogue.innerHTML = "";
    userInput.disabled = false;
    sendBtn.disabled = false;
    endBtn.disabled = false;
    startForm.querySelectorAll("input,button[type=submit]").forEach(x => x.disabled = true);
    // Le contexte initial est déjà append côté backend, on déclenche le 1er stream.
    streamResponse();
  } catch (e) { alert("Erreur réseau: " + e.message); }
});

// ============================================================ Send + stream

sendBtn.addEventListener("click", sendUserMessage);
userInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendUserMessage(); }
});

async function sendUserMessage() {
  const text = userInput.value.trim();
  if (!text) return;
  userInput.value = "";
  appendTurn("student", text);
  try {
    const r = await fetch("/api/send_message", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text}),
    });
    if (!r.ok && r.status !== 202) {
      const data = await r.json().catch(() => ({}));
      alert("Erreur send: " + (data.error || r.status));
      return;
    }
    streamResponse();
  } catch (e) { alert("Erreur réseau: " + e.message); }
}

function streamResponse() {
  if (currentEventSource) currentEventSource.close();
  currentClaudeTurn = appendTurn("claude", "");
  const es = new EventSource("/api/stream_response");
  currentEventSource = es;
  es.addEventListener("text", (e) => {
    const chunk = JSON.parse(e.data);
    currentClaudeTurn.textContent += chunk;
    dialogue.scrollTop = dialogue.scrollHeight;
  });
  es.addEventListener("tts", (e) => {
    // Phase A : pas de TTS audio (Edge TTS / Piper en Phase B).
    // On marque la phrase TTS visuellement.
    const chunk = JSON.parse(e.data);
    const span = document.createElement("span");
    span.style.fontWeight = "600";
    span.textContent = chunk;
    currentClaudeTurn.appendChild(span);
  });
  es.addEventListener("end", () => { es.close(); currentEventSource = null; finishSession(); });
  es.addEventListener("done", () => { es.close(); currentEventSource = null; });
  es.addEventListener("error", (e) => {
    let info = ""; try { info = e.data ? JSON.parse(e.data).message : ""; } catch (_) {}
    appendTurn("system", "[Erreur stream] " + (info || "connexion perdue"));
    es.close(); currentEventSource = null;
  });
}

function appendTurn(role, text) {
  if (dialogue.querySelector(".placeholder")) dialogue.innerHTML = "";
  const div = document.createElement("div");
  div.className = "turn " + role;
  const r = document.createElement("div");
  r.className = "role";
  r.textContent = role === "student" ? "Toi" : role === "claude" ? "Compagnon" : "Système";
  const t = document.createElement("div");
  t.textContent = text;
  div.appendChild(r); div.appendChild(t);
  dialogue.appendChild(div);
  dialogue.scrollTop = dialogue.scrollHeight;
  return t;
}

// ============================================================ End session

endBtn.addEventListener("click", () => finishSession(false));

async function finishSession(autoFromEnd = true) {
  if (!activeSession) return;
  if (currentEventSource) { currentEventSource.close(); currentEventSource = null; }
  try {
    const r = await fetch("/api/end_session", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({interrupted: !autoFromEnd && false}),
    });
    const data = await r.json();
    if (r.ok) {
      appendTurn("system",
        `Séance terminée. Durée: ${data.duration_seconds}s, points faibles: ${data.weak_points_count}.`);
    }
  } catch (e) { /* silencieux */ }
  activeSession = null;
  userInput.disabled = true;
  sendBtn.disabled = true;
  endBtn.disabled = true;
  startForm.querySelectorAll("input,button[type=submit]").forEach(x => x.disabled = false);
  sessionInfo.textContent = "";
}

// ============================================================ Push-to-talk indicator (visuel uniquement)
// Le hook ESPACE est côté Python (listener.py) — ici on visualise juste si le
// backend nous indique le passage en mode recording. Phase A : pas de canal
// dédié, donc on laisse statique. À brancher en Phase B si besoin.

document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && document.activeElement !== userInput) {
    recordIndicator.classList.add("active");
  }
});
document.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    recordIndicator.classList.remove("active");
  }
});
