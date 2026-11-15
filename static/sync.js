// js/sync.js — talks to the calendar backend over HTTP.
//
// This is the original application's request flow: mutators in state.js
// fire-and-forget into this module after applying the change locally, so the
// UI never blocks on the network. The poll loop hits the cheap version
// endpoint every POLL_MS and only fetches the full snapshot when the counter
// has moved, so idle devices cost almost nothing.
//
// Ported differences:
//   - the API lives under the hub's app prefix (data-api-base, so no fixed
//     root path is hard-coded);
//   - there is no login header and no 401 handling: the hub is open;
//   - a failed request is shown to the user instead of only being logged,
//     because the hub server now validates what it stores.

const POLL_MS = 5000;

function apiBase() {
  const root = document.querySelector(".cal-app");
  return (root && root.dataset.apiBase) || "api";
}

const API = apiBase();

let _pollTimer = null;
let _onRemoteChange = null;     // (newState) => void   — provided by state.js
let _getLocalVersion = () => 0; // () => number          — provided by state.js
let _onVersion = null;          // (v) => void           — bumps state.js's _serverVersion

let _notice = null;
let _noticeTimer = null;
let _resyncing = false;

/** state.js calls this once at boot to be notified of every server version
 *  it sees, so it can avoid re-fetching state that originated from us. */
export function onServerVersion(fn) { _onVersion = fn; }

function showNotice(message) {
  const root = document.querySelector(".cal-app");
  if (!root) return;
  if (!_notice) {
    _notice = document.createElement("div");
    _notice.className = "cal-notice";
    _notice.setAttribute("role", "alert");
    root.append(_notice);
  }
  _notice.textContent = message;
  _notice.dataset.open = "true";
  clearTimeout(_noticeTimer);
  _noticeTimer = setTimeout(() => {
    if (_notice) _notice.dataset.open = "false";
  }, 6000);
}

function clearNotice() {
  clearTimeout(_noticeTimer);
  if (_notice) _notice.dataset.open = "false";
}

// ---------- low-level request ----------

async function req(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(API + path, opts);
  } catch (e) {
    showNotice("Can't reach the calendar. Your change is on this device only.");
    throw e;
  }

  const text = await res.text();
  let parsed = null;
  if (text) {
    try { parsed = JSON.parse(text); } catch { parsed = null; }
  }

  if (!res.ok) {
    const message =
      (parsed && parsed.error) ||
      "The calendar could not save that change just now.";
    showNotice(message);
    // A rejected write leaves an optimistic change on screen that the server
    // never stored. Put the shared data back in charge so the UI does not keep
    // showing something that was refused.
    if (method !== "GET" && res.status < 500) resync();
    throw new Error(`${method} ${path} → HTTP ${res.status}: ${message}`);
  }

  // Only a successful write clears the notice: the 5s poll reads state
  // constantly and must not wipe an error message the person is reading.
  if (method !== "GET") clearNotice();
  if (parsed && typeof parsed.version === "number" && _onVersion) {
    _onVersion(parsed.version);
  }
  return parsed;
}

/** Re-read the shared snapshot and adopt it (drops a refused local change). */
async function resync() {
  if (_resyncing || !_onRemoteChange) return;
  _resyncing = true;
  try {
    const remote = await fetchState();
    if (remote) _onRemoteChange(remote);
  } catch {
    // stay on the local copy; the notice already explains the failure
  } finally {
    _resyncing = false;
  }
}

// ---------- read ----------

export async function fetchState() {
  return req("GET", "/state");
}

export async function fetchVersion() {
  const r = await req("GET", "/version");
  return r ? r.version : null;
}

// ---------- write (one per mutator in state.js) ----------

export function pushEvent(ev) {
  return req("POST", "/events", ev).catch(warn("pushEvent"));
}
export function patchEvent(id, patch) {
  return req("PATCH", `/events/${encodeURIComponent(id)}`, patch).catch(warn("patchEvent"));
}
export function deleteEventRemote(id) {
  return req("DELETE", `/events/${encodeURIComponent(id)}`).catch(warn("deleteEvent"));
}
export function pushCalendar(cal) {
  return req("POST", "/calendars", cal).catch(warn("pushCalendar"));
}
export function patchCalendar(id, patch) {
  return req("PATCH", `/calendars/${encodeURIComponent(id)}`, patch).catch(warn("patchCalendar"));
}
export function deleteCalendarRemote(id) {
  return req("DELETE", `/calendars/${encodeURIComponent(id)}`).catch(warn("deleteCalendar"));
}
export function pushReplace(data) {
  return req("PUT", "/state", data).catch(warn("pushReplace"));
}

function warn(label) {
  return (e) => {
    console.warn(`[sync] ${label} failed:`, e.message);
    // swallow — the mutator already applied locally; the next poll reconciles
  };
}

// ---------- initial reconciliation ----------

/**
 * The shared database is always authoritative, so boot simply adopts it.
 * Until it answers, the UI is painted from this device's cache.
 */
export async function initialSync(applyRemote) {
  try {
    const remote = await fetchState();
    if (remote) applyRemote(remote);
  } catch (e) {
    console.warn("[sync] could not load the shared calendar:", e.message);
  }
}

// ---------- polling ----------

async function pollNow() {
  try {
    const v = await fetchVersion();
    if (v == null) return;
    if (v > _getLocalVersion()) {
      const remote = await fetchState();
      if (remote && _onRemoteChange) _onRemoteChange(remote);
    }
  } catch {
    // the failure notice is already shown by req()
  }
}

export function startPolling({ getLocalVersion, onRemoteChange }) {
  _getLocalVersion = getLocalVersion;
  _onRemoteChange = onRemoteChange;

  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(pollNow, POLL_MS);

  // Poll immediately when the user returns to the tab — feels live without
  // having to wait for the next interval tick.
  window.addEventListener("focus", pollNow);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollNow();
  });
  // iOS Safari restores pages from its back/forward cache without firing
  // focus or visibilitychange. pageshow.persisted === true means we're
  // looking at a frozen snapshot that may be stale — force a poll.
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) pollNow();
  });
}
