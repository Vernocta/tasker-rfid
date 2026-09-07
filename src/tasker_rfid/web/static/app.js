/* Shared helpers for the dashboard screens.
 *
 * Small on purpose. Every screen does the same three things: ask the API
 * for something, draw it into a table, and do that again in a few
 * seconds. If you want to change what a screen shows, the code that
 * builds its rows is in the screen's own template, next to the HTML it
 * fills in.
 */

const API = window.TASKER.apiBase;
const POLL_SECONDS = window.TASKER.pollSeconds;

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
  if (!response.ok) {
    throw new Error(data.detail || `${response.status} from ${path}`);
  }
  return data;
}

/* Run a screen's refresh now, then every POLL_SECONDS. A failure is shown
 * in the header rather than thrown away, so a dead API looks dead
 * instead of looking like an empty warehouse. */
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
    el.textContent = "Cannot reach the system — showing the last figures received";
    el.className = "text-xl font-semibold text-red-700";
    return;
  }
  el.textContent = "Updated " + new Date().toLocaleTimeString();
  el.className = "text-xl text-slate-500";
}

/* Formatting. Times are shown in the browser's own timezone, which is the
 * warehouse's. */
function clockTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function dayAndTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString([], { day: "2-digit", month: "short" }) + " " + clockTime(iso);
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

/* Replace a table body, or show a sentence explaining what would fill it. */
function fillTable(tbodyId, rows, buildRow, emptyMessage, columns) {
  const tbody = document.getElementById(tbodyId);
  if (!rows.length) {
    tbody.innerHTML =
      `<tr><td colspan="${columns}" class="py-10 text-2xl text-slate-500">${text(emptyMessage)}</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(buildRow).join("");
}

/* Container statuses as words, not database constants. */
const STATUS_LABELS = {
  REGISTERED: "Registered",
  IN_STOCK: "In stock",
  DISPATCHED: "Dispatched",
};

function statusWords(status) {
  return STATUS_LABELS[status] || status || "";
}

/* An anomaly's detail is a bag of whatever the service recorded. Show it
 * as readable pairs rather than raw JSON: this is read from across a
 * room, by someone deciding what to do about it. */
const DETAIL_LABELS = {
  portal: "Portal",
  direction: "Direction",
  status: "Status was",
  declared_children: "Boxes on the pallet",
  children_read: "Boxes read",
  missing: "Missing",
  variance: "Variance",
  cycle_id: "Cycle count",
  last_portal: "Last seen at",
  corrected_from: "Corrected from",
  corrected_to: "Corrected to",
  resolution_note: "Note",
};

function detailWords(detail) {
  if (!detail) return { reason: "", pairs: [] };
  const reason = detail.reason || "";
  const pairs = Object.entries(detail)
    .filter(([key, value]) => key !== "reason" && value !== null && value !== "")
    .map(([key, value]) => {
      const label = DETAIL_LABELS[key] || key.replace(/_/g, " ");
      return `${label}: ${key === "status" ? statusWords(value) : value}`;
    });
  return { reason, pairs };
}

/* Anomaly kinds in words an operator can act on. */
const ANOMALY_LABELS = {
  UNKNOWN_TID: "Unknown tag",
  ILLEGAL_TRANSITION: "Movement not allowed",
  NO_DIRECTION: "Direction unclear",
  NO_SESSION: "No customer selected",
  SHORT_PALLET: "Pallet short of boxes",
  COUNT_MISMATCH: "Cycle count variance",
};

const ANOMALY_EXPLANATIONS = {
  UNKNOWN_TID: "A tag was read that is not registered to any container.",
  ILLEGAL_TRANSITION: "The movement does not fit where the container was. Someone needs to decide what happened.",
  NO_DIRECTION: "The exit beams could not tell which way it went, so nothing was moved.",
  NO_SESSION: "Something was read at the exit with no customer selected. It was not dispatched.",
  SHORT_PALLET: "Fewer boxes were read than are on the pallet.",
  COUNT_MISMATCH: "A cycle count found something different from the records.",
};
