import { currency } from "./store.js";

const fmtCache = new Map();
function formatter(code, digits) {
  const key = `${code}:${digits}`;
  if (!fmtCache.has(key)) {
    try {
      fmtCache.set(key, new Intl.NumberFormat(undefined, { style: "currency", currency: code, maximumFractionDigits: digits, minimumFractionDigits: digits }));
    } catch {
      fmtCache.set(key, new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: digits }));
    }
  }
  return fmtCache.get(key);
}

/** Format a USD amount in the user's display currency. */
export function money(usd, { compact = false } = {}) {
  if (usd == null || Number.isNaN(usd)) return "—";
  const { code, rate } = currency.value;
  const v = usd * (rate || 1);
  if (compact && Math.abs(v) >= 10000) {
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: code, notation: "compact", maximumFractionDigits: 1 }).format(v);
    } catch {
      /* fall through */
    }
  }
  const digits = Math.abs(v) >= 1000 ? 0 : Math.abs(v) >= 100 ? 0 : 2;
  return formatter(code, digits).format(v);
}

export const toDisplay = (usd) => (usd == null ? null : usd * (currency.value.rate || 1));
export const fromDisplay = (v) => (v == null || v === "" ? null : Number(v) / (currency.value.rate || 1));

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "short" });
export function ago(ts) {
  if (!ts) return "";
  const s = Math.round(ts - Date.now() / 1000);
  const abs = Math.abs(s);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(s / 86400), "day");
  return new Date(ts * 1000).toLocaleDateString();
}

export function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function shortTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return clock(ts);
  const diff = (now - d) / 86400000;
  if (diff < 6) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

export function dayLabel(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}

export function accountAge(memberSince) {
  if (!memberSince) return null;
  const days = (Date.now() / 1000 - memberSince) / 86400;
  if (days < 1) return "today";
  if (days < 60) return `${Math.round(days)} d`;
  if (days < 365) return `${Math.round(days / 30)} mo`;
  const y = days / 365.25;
  return `${y < 10 ? y.toFixed(1).replace(/\.0$/, "") : Math.round(y)} yr`;
}

export const ageDays = (memberSince) => (memberSince ? (Date.now() / 1000 - memberSince) / 86400 : null);

// Steam level ring colours (by tens; 100+ cycles)
const LEVEL_COLORS = ["#9b9b9b", "#c02942", "#d95b43", "#fecc23", "#467a3c", "#4e8ddb", "#7652c9", "#c252c9", "#542437", "#997c52"];
export function levelColor(level) {
  if (level == null) return "var(--outline-variant)";
  return LEVEL_COLORS[Math.floor(level / 10) % 10];
}

export const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
