/* Shared helpers for the dashboard screens.
 *
 * Small on purpose. Every screen does the same three things: ask the API
 * for something, draw it into a list, and do that again in a few seconds.
 * If you want to change what a screen shows, the code that builds its rows
 * is in the screen's own template, next to the HTML it fills in.
 *
 * Every word shown to a person comes from window.TASKER.text, which the
 * server filled in for the chosen language. To change wording, edit
 * web/text.py, not this file.
 */

const API = window.TASKER.apiBase;
const POLL_SECONDS = window.TASKER.pollSeconds;
const TEXT = window.TASKER.text;

/* Look up a phrase, filling in any {placeholders}. */
function t(key, values) {
  let phrase = TEXT[key];
  if (phrase === undefined) return key;   // loud rather than blank
  if (values) {
    for (const [name, value] of Object.entries(values)) {
      phrase = phrase.split("{" + name + "}").join(value);
    }
  }
  return phrase;
}

/* Fetch JSON from the API. Throws on anything that is not a 2xx so the
 * caller can show the operator that the screen is stale. */
async function apiGet(path, params) {
  const url = new URL(API + path);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== "") url.searchParams.set(k, v);
  });
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} from ${path}`);
  return response.json();
}

async function apiPost(path, body) {
  const response = await fetch(API + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `${response.status} from ${path}`);
  return data;
}

/* Run a screen's refresh now, then every POLL_SECONDS. A failure is shown
 * in the header rather than thrown away, so a dead API looks dead instead
 * of looking like an empty warehouse. */
function startPolling(refresh) {
  const run = async () => {
    try {
      await refresh();
      markUpdated(null);
    } catch (error) {
      markUpdated(error);
    }
  };
  run();
  setInterval(run, POLL_SECONDS * 1000);
}

function markUpdated(error) {
  const el = document.getElementById("updated");
  if (!el) return;
  if (error) {
    el.textContent = t("shell.offline");
    el.className = "text-[clamp(12px,1.6vh,20px)] font-bold text-red-700 max-w-[32ch] leading-tight";
    return;
  }
  el.textContent = t("shell.updated") + " " + clockTime(new Date().toISOString());
  el.className = "text-[clamp(12px,1.6vh,20px)] text-neutral-500 whitespace-nowrap";
}

/* ---- Formatting. Times are shown in the browser's own timezone, which
   is the warehouse's. ---- */

/* 24-hour, always. Argentina writes the time that way, and "06:33:10 PM"
 * is three characters wider than a column sized for a clock. */
function clockTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("es-AR", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

function shortClock(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("es-AR", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function dayAndTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString([], { day: "2-digit", month: "short" }) + " " + clockTime(iso);
}

/* "14 s", "12 min", "2 h" — how long ago something happened. The sentence
 * around it differs by language, so this returns only the duration. */
function sinceWords(iso) {
  if (!iso) return null;
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 90) return t("time.seconds", { n: seconds });
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return t("time.minutes", { n: minutes });
  return t("time.hours", { n: Math.round(minutes / 60) });
}

function number(value) {
  return (value === null || value === undefined) ? "—" : Number(value).toLocaleString();
}

/* Never build HTML from values that came out of the database by pasting
 * them into a string: a TID or a customer name with a < in it would
 * become markup. */
function text(value) {
  const div = document.createElement("div");
  div.textContent = value === null || value === undefined ? "" : String(value);
  return div.innerHTML;
}

/* Replace a scrolling list, or show a sentence explaining what would fill
 * it. Never "No data". */
function fillList(containerId, rows, buildRow, emptyMessage) {
  const box = document.getElementById(containerId);
  if (!box) return;
  box.innerHTML = rows.length
    ? rows.map(buildRow).join("")
    : `<div class="px-2 py-[3vh] t-lede text-neutral-500 max-w-[60ch]">${text(emptyMessage)}</div>`;
}

/* ---- Words rather than database constants ---- */

function statusWords(status) {
  return status ? t("status." + status) : "";
}

function anomalyLabel(kind)       { return t("kind." + kind); }
function anomalyExplanation(kind) { return t("why." + kind); }

/* An anomaly's detail is a bag of whatever the service recorded. Show it
 * as readable pairs rather than raw JSON: this is read from across a room,
 * by someone deciding what to do about it. */
function detailWords(detail) {
  if (!detail) return { reason: "", pairs: [] };
  const reason = detail.reason || "";
  const pairs = Object.entries(detail)
    .filter(([key, value]) => key !== "reason" && value !== null && value !== "")
    .map(([key, value]) => {
      const label = TEXT["detail." + key] || key.replace(/_/g, " ");
      return `${label}: ${key === "status" ? statusWords(value) : value}`;
    });
  return { reason, pairs };
}
