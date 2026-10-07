// Global reactive state (Preact signals) + server event reducers.
import { signal, computed, batch } from "../vendor/preact.js";
import { api } from "./api.js";

export const online = signal(false); // websocket to our server
export const booted = signal(false);
export const auth = signal(null);
export const settings = signal(null);
export const demo = signal(false);
export const version = signal("");
export const invites = signal(new Map());
export const invitesMeta = signal({});
export const chatStatus = signal({ state: "offline" });
export const prices = signal(null);
export const providers = signal([]);
export const currency = signal({ code: "USD", rate: 1 });
export const history = signal([]);
export const broadcasts = signal(new Map());
export const personas = signal(new Map());
export const conversations = signal([]);
export const conversationsLoaded = signal(false);
export const messages = signal(new Map()); // partner -> {items, more, loaded, loading}
export const typing = signal(new Map()); // partner -> expires (ms)
export const unread = signal(new Map()); // partner -> count
export const selection = signal(new Set());
export const toasts = signal([]);
export const detailId = signal(null);
export const lightbox = signal(null);
export const dialog = signal(null); // {type, props}

// ------------------------------------------------------------------ routing
function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  return { name: parts[0] || "requests", id: parts[1] || null };
}
export const route = signal(parseHash());
window.addEventListener("hashchange", () => (route.value = parseHash()));
export const go = (name, id) => (location.hash = `#/${name}${id ? `/${id}` : ""}`);

export const me = computed(() => auth.value?.steamid || null);
export const authed = computed(() => auth.value?.state === "authenticated");
export const pendingInvites = computed(() => [...invites.value.values()].filter((r) => r.status === "pending"));
export const unreadTotal = computed(() => [...unread.value.values()].reduce((a, b) => a + b, 0));

// ------------------------------------------------------------------ toasts
let toastId = 0;
export function toast(text, opts = {}) {
  const id = ++toastId;
  const t = { id, text, kind: opts.kind || "info", action: opts.action };
  toasts.value = [...toasts.value.slice(-3), t];
  setTimeout(() => dismissToast(id), opts.timeout ?? (t.kind === "error" ? 7000 : 4200));
  return id;
}
export const toastError = (e) => toast(e?.message || String(e), { kind: "error" });
export function dismissToast(id) {
  toasts.value = toasts.value.filter((t) => t.id !== id);
}

// ------------------------------------------------------------------ helpers
function setMap(sig, key, value) {
  const m = new Map(sig.value);
  if (value === undefined) m.delete(key);
  else m.set(key, value);
  sig.value = m;
}

export function rememberPersonas(list) {
  const m = new Map(personas.value);
  let changed = false;
  for (const p of list || []) {
    if (!p?.steamid || !p.name) continue;
    m.set(p.steamid, { ...(m.get(p.steamid) || {}), ...p });
    changed = true;
  }
  if (changed) personas.value = m;
}

let convReload = null;
function reloadConversationsSoon() {
  clearTimeout(convReload);
  convReload = setTimeout(loadConversations, 800);
}

export function personaOf(steamid) {
  const p = personas.value.get(String(steamid));
  if (p) return p;
  const rec = invites.value.get(String(steamid));
  const conv = conversations.value.find((c) => c.partner === String(steamid));
  return { steamid: String(steamid), name: rec?.name || conv?.persona?.name || "", avatar: rec?.avatar || conv?.persona?.avatar || "" };
}

// ------------------------------------------------------------------ snapshot / events
export function applySnapshot(s) {
  batch(() => {
    version.value = s.version;
    demo.value = s.demo;
    auth.value = s.auth;
    settings.value = s.settings;
    chatStatus.value = s.chat;
    prices.value = s.prices;
    providers.value = s.providers;
    currency.value = s.currency;
    history.value = s.history || [];
    broadcasts.value = new Map((s.broadcasts || []).map((b) => [b.id, b]));
    invites.value = new Map(s.invites.items.map((r) => [r.steamid, r]));
    const { items, ...meta } = s.invites;
    invitesMeta.value = meta;
    booted.value = true;
  });
}

export function handleEvent(ev) {
  const d = ev.data;
  switch (ev.type) {
    case "snapshot":
      applySnapshot(d);
      if (authed.value) loadConversations();
      break;
    case "auth": {
      const was = auth.value?.state;
      auth.value = d;
      if (d.state === "authenticated" && was !== "authenticated") loadConversations();
      if (d.state !== "authenticated") {
        conversations.value = [];
        conversationsLoaded.value = false;
        messages.value = new Map();
      }
      break;
    }
    case "settings":
      settings.value = d;
      break;
    case "prices":
      prices.value = d;
      break;
    case "currency":
      currency.value = d;
      break;
    case "chat:status":
      chatStatus.value = d;
      break;
    case "invite":
      setMap(invites, d.steamid, d);
      if (d.status !== "pending" && selection.value.has(d.steamid)) {
        const s = new Set(selection.value);
        s.delete(d.steamid);
        selection.value = s;
      }
      break;
    case "invites:reset":
      invites.value = new Map(d.map((r) => [r.steamid, r]));
      break;
    case "invites:meta":
      invitesMeta.value = d;
      break;
    case "history":
      history.value = [...history.value.slice(-999), d];
      break;
    case "broadcast":
      setMap(broadcasts, d.id, d);
      break;
    case "persona": {
      const prev = personas.value.get(d.steamid) || {};
      setMap(personas, d.steamid, { ...prev, ...d });
      break;
    }
    case "chat:typing":
      setMap(typing, d.partner, Date.now() + 6500);
      setTimeout(() => {
        if ((typing.value.get(d.partner) || 0) <= Date.now()) setMap(typing, d.partner, undefined);
      }, 6600);
      break;
    case "chat:message":
      onChatMessage(d);
      break;
    case "friends":
      break;
  }
}

function onChatMessage(m) {
  const partner = m.partner;
  batch(() => {
    const thread = messages.value.get(partner);
    if (thread) {
      const items = thread.items.filter((x) => x.id !== m.id && !(x.pending && x.outgoing && m.outgoing && x.text === m.text));
      items.push(m);
      items.sort((a, b) => a.timestamp - b.timestamp || a.ordinal - b.ordinal);
      setMap(messages, partner, { ...thread, items });
    }
    if (!m.outgoing) setMap(typing, partner, undefined);
    // conversation list
    const list = conversations.value.filter((c) => c.partner !== partner);
    const prev = conversations.value.find((c) => c.partner === partner);
    if (!prev && !personas.value.get(partner)?.name) reloadConversationsSoon();
    const isActive = route.value.name === "chats" && route.value.id === partner && document.visibilityState === "visible";
    list.unshift({ ...(prev || { partner, persona: personOrEmpty(partner) }), last_message: m.timestamp, preview: m.text, preview_out: m.outgoing });
    conversations.value = list;
    if (!m.outgoing && !isActive) {
      setMap(unread, partner, (unread.value.get(partner) || 0) + 1);
      notify(partner, m.text);
    }
  });
}

function personOrEmpty(partner) {
  const p = personaOf(partner);
  return p.name ? p : { steamid: partner, name: "", avatar: "" };
}

function notify(partner, text) {
  if (document.visibilityState === "visible") return;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const p = personaOf(partner);
  try {
    const n = new Notification(p.name || "New Steam message", { body: stripBB(text), icon: p.avatar || undefined, tag: partner });
    n.onclick = () => {
      window.focus();
      go("chats", partner);
    };
  } catch {
    /* ignore */
  }
}

export const stripBB = (t) =>
  (t || "")
    .replace(/\[img[^\]]*\][^[]*\[\/img\]/g, "🖼 Image")
    .replace(/\[emoticon\]([^[]+)\[\/emoticon\]/g, ":$1:")
    .replace(/\[sticker[^\]]*\]\[\/sticker\]/g, "Sticker")
    .replace(/\[\/?[a-z]+[^\]]*\]/g, "")
    .replace(/\\\[/g, "[");

// ------------------------------------------------------------------ chat loading
export async function loadConversations() {
  try {
    const list = await api("/chat/conversations");
    batch(() => {
      conversations.value = list.map((c) => {
        const prev = conversations.value.find((x) => x.partner === c.partner);
        return { ...c, preview: c.preview ?? prev?.preview, preview_out: c.preview_out ?? prev?.preview_out };
      });
      const u = new Map(unread.value);
      for (const c of list) if (c.unread) u.set(c.partner, c.unread);
      unread.value = u;
      for (const c of list) {
        if (c.persona?.name && !personas.value.has(c.partner)) setMap(personas, c.partner, c.persona);
      }
      conversationsLoaded.value = true;
    });
  } catch (e) {
    conversationsLoaded.value = true;
    if (e.status !== 401) console.warn("conversations", e);
  }
}

export async function loadMessages(partner, older = false) {
  const thread = messages.value.get(partner) || { items: [], more: true, loaded: false };
  if (thread.loading || (older && !thread.more)) return;
  setMap(messages, partner, { ...thread, loading: true });
  try {
    let q = "";
    if (older && thread.items.length) {
      const first = thread.items[0];
      q = `?before_ts=${first.timestamp}&before_ordinal=${first.ordinal}`;
    }
    const res = await api(`/chat/${partner}/messages${q}`);
    const cur = messages.value.get(partner) || thread;
    const map = new Map(cur.items.map((m) => [m.id, m]));
    for (const m of res.messages) map.set(m.id, m);
    const items = [...map.values()].sort((a, b) => a.timestamp - b.timestamp || a.ordinal - b.ordinal);
    setMap(messages, partner, { items, more: res.more, loaded: true, loading: false });
    const last = res.messages[res.messages.length - 1];
    if (!older && last) {
      const conv = conversations.value.find((c) => c.partner === partner);
      if (conv && !conv.preview) {
        conversations.value = conversations.value.map((c) => (c.partner === partner ? { ...c, preview: last.text, preview_out: last.outgoing } : c));
      }
    }
  } catch (e) {
    setMap(messages, partner, { ...thread, loading: false, loaded: true, error: e.message });
  }
}

export function markRead(partner) {
  const thread = messages.value.get(partner);
  const last = thread?.items[thread.items.length - 1];
  if (unread.value.get(partner)) setMap(unread, partner, undefined);
  if (last) api(`/chat/${partner}/ack`, { method: "POST", body: { timestamp: last.timestamp } }).catch(() => {});
}

export function addPendingMessage(partner, text) {
  const thread = messages.value.get(partner) || { items: [], more: false, loaded: true };
  const m = { id: `pending-${Date.now()}`, partner, sender: me.value, outgoing: true, text, timestamp: Math.floor(Date.now() / 1000), ordinal: 9999, pending: true };
  setMap(messages, partner, { ...thread, items: [...thread.items, m] });
  return m.id;
}

export function failPendingMessage(partner, id, error) {
  const thread = messages.value.get(partner);
  if (!thread) return;
  setMap(messages, partner, { ...thread, items: thread.items.map((m) => (m.id === id ? { ...m, failed: error } : m)) });
}

export function removeMessage(partner, id) {
  const thread = messages.value.get(partner);
  if (!thread) return;
  setMap(messages, partner, { ...thread, items: thread.items.filter((m) => m.id !== id) });
}
