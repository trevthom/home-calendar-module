// js/storage.js — localStorage cache for an instant first paint.
//
// The shared database is the source of truth; this cache only lets the
// calendar paint before the first request finishes (and stay readable if the
// connection drops). It is never pushed back to the server, so a stale cache
// on one device can't resurrect data another device deleted.
//
// Theme is a device preference, stored separately, as before.

const BASE = "calendar-app:v1";
const DATA_KEY = BASE + ":data";

export function load() {
  try {
    const raw = localStorage.getItem(DATA_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function save(data) {
  try {
    localStorage.setItem(DATA_KEY, JSON.stringify(data));
  } catch (e) {
    console.warn("Storage save failed:", e);
  }
}

export function loadTheme() {
  return localStorage.getItem(BASE + ":theme") || "light";
}

export function saveTheme(theme) {
  localStorage.setItem(BASE + ":theme", theme);
}
