import { html, useEffect } from "../../vendor/preact.js";
import {
  booted, authed, auth, route, go, settings, pendingInvites, unreadTotal, chatStatus, online, demo, personas, selection, dialog, toastError,
} from "../store.js";
import { post } from "../api.js";
import { applyTheme, seedFromImage, DEFAULT_SEED } from "../theme.js";
import { Icon, BrandMark } from "../icons.js";
import { Loader, Toasts, Lightbox, MenuButton, Avatar } from "./ui.js";
import { LoginScreen } from "./login.js";
import { RequestsPage } from "./requests.js";
import { DetailSheet } from "./detail.js";
import { ChatPage } from "./chat.js";
import { AutomationPage } from "./automation.js";
import { ActivityPage } from "./activity.js";
import { SettingsPage } from "./settings.js";
import { Dialogs } from "./dialogs.js";

const PAGES = {
  requests: { label: "Requests", icon: "person_add", title: "Friend requests", Page: RequestsPage },
  chats: { label: "Chats", icon: "forum", title: "Chats", Page: ChatPage, flush: true },
  automation: { label: "Rules", icon: "bolt", title: "Automation", Page: AutomationPage },
  activity: { label: "Activity", icon: "history", title: "Activity", Page: ActivityPage },
  settings: { label: "Settings", icon: "settings", title: "Settings", Page: SettingsPage },
};

function useTheme() {
  const s = settings.value;
  const meAvatar = personas.value.get(auth.value?.steamid || "")?.avatar;
  useEffect(() => {
    if (!s) return;
    let cancelled = false;
    const apply = (seed) => !cancelled && applyTheme({ theme: s.theme, seed: seed || DEFAULT_SEED, style: s.palette });
    if (s.accent) apply(s.accent);
    else {
      apply(null);
      seedFromImage(meAvatar).then((seed) => seed && apply(seed));
    }
    return () => (cancelled = true);
  }, [s?.theme, s?.accent, s?.palette, meAvatar]);
}

function useTitle() {
  const n = pendingInvites.value.length;
  const u = unreadTotal.value;
  useEffect(() => {
    const parts = [];
    if (u) parts.push(`💬${u}`);
    if (n) parts.push(`(${n})`);
    document.title = `${parts.join(" ")} MFRAN · Steam friend requests`.trim();
  }, [n, u]);
}

function NavItems() {
  const r = route.value.name;
  const counts = { requests: pendingInvites.value.length, chats: unreadTotal.value };
  return Object.entries(PAGES).map(([id, p]) => html`<button key=${id} class=${`rail-item ${r === id ? "active" : ""}`} aria-current=${r === id ? "page" : undefined} onClick=${() => go(id)}>
    <span class="pill"><${Icon} name=${p.icon} fill=${r === id} /></span>
    <span class="lbl">${p.label}</span>
    ${counts[id] ? html`<span class=${`badge ${id === "requests" ? "" : ""}`} style=${id === "requests" ? "background:var(--primary);color:var(--on-primary)" : ""}>${counts[id] > 99 ? "99+" : counts[id]}</span>` : null}
  </button>`);
}

function ChatChip() {
  const st = chatStatus.value;
  if (!online.value) return html`<span class="conn error" title="Lost connection to the MFRAN server"><span class="dot"></span>Server offline</span>`;
  const label = st.transport === "demo" ? "Demo" : st.state === "online" ? "Chat live" : st.state === "connecting" ? "Connecting" : "Chat polling";
  const cls = st.state === "online" ? "online" : st.state === "connecting" ? "connecting" : "error";
  return html`<span class=${`conn ${cls}`} title=${st.error || ""}><span class="dot"></span>${label}</span>`;
}

function Topbar({ page }) {
  const a = auth.value || {};
  const me = personas.value.get(a.steamid) || {};
  const sub = { requests: `${pendingInvites.value.length} waiting`, chats: unreadTotal.value ? `${unreadTotal.value} unread` : "" }[route.value.name];
  return html`<header class="topbar">
    <div class="col grow" style="gap:0">
      <h1 class="headline-m">${page.title}</h1>
      ${sub ? html`<span class="body-s muted">${sub}</span>` : null}
    </div>
    ${demo.value ? html`<span class="tag tertiary demo-tag" style="height:32px">Demo data</span>` : null}
    <${ChatChip} />
    <${MenuButton} class="icon-btn" label="Account" items=${[
      { icon: "account_circle", label: me.name || a.account_name || "Account", onClick: () => go("settings") },
      { icon: "open_in_new", label: "My Steam profile", href: `https://steamcommunity.com/profiles/${a.steamid}/` },
      "sep",
      { icon: "logout", label: "Sign out", danger: true, onClick: () => post("/auth/logout").catch(toastError) },
    ]}><${Avatar} src=${me.avatar} size=${36} round name=${me.name || a.account_name} /><//>
  </header>`;
}

export function App() {
  useTheme();
  useTitle();
  useEffect(() => {
    const h = (e) => {
      if (e.key === "Escape" && selection.value.size && !dialog.value) selection.value = new Set();
    };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, []);
  if (!booted.value) {
    return html`<div class="aurora"><i></i><i></i><i></i></div>
      <div class="login-wrap"><div class="col" style="align-items:center;gap:18px"><${BrandMark} lg /><${Loader} size=${56} contained />
      <span class="body-m muted">${online.value ? "Loading…" : "Connecting to MFRAN…"}</span></div></div><${Toasts} />`;
  }
  if (!authed.value) {
    return html`<div class="aurora"><i></i><i></i><i></i></div><${LoginScreen} /><${Toasts} />`;
  }
  const page = PAGES[route.value.name] || PAGES.requests;
  const P = page.Page;
  return html`<div class="aurora"><i></i><i></i><i></i></div>
    <div class=${`app ${selection.value.size ? "has-float" : ""}`}>
      <nav class="rail glass" aria-label="Main">
        <div class="brand"><${BrandMark} /></div>
        <${NavItems} />
        <div class="bottom">
          <button class="icon-btn" title="Refresh friend requests" aria-label="Refresh" onClick=${() => post("/invites/refresh").catch(toastError)}><${Icon} name="refresh" /></button>
        </div>
      </nav>
      <main class="main">
        <${Topbar} page=${page} />
        <div class=${`page ${page.flush ? "flush" : ""}`}><${P} /></div>
      </main>
      <nav class="bottom-nav glass" aria-label="Main"><${NavItems} /></nav>
    </div>
    <${DetailSheet} />
    <${Dialogs} />
    <${Lightbox} />
    <${Toasts} />`;
}
