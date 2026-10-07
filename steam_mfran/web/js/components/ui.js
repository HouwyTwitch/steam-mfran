import { html, render, useState, useEffect, useRef, useLayoutEffect } from "../../vendor/preact.js";
import { Icon } from "../icons.js";
import { toasts, dismissToast, lightbox } from "../store.js";
import { levelColor } from "../format.js";

export { Icon };

export function Avatar({ src, size = 40, status, round, alt = "", name = "" }) {
  const [err, setErr] = useState(false);
  useEffect(() => setErr(false), [src]);
  const cls = status === 1 ? "online" : status === "ingame" ? "ingame" : status >= 2 && status <= 4 ? "away" : "";
  const initial = (Array.from((name || alt || "").trim())[0] || "?").toUpperCase();
  return html`<div class=${`avatar ${round ? "round" : ""}`} style=${`--s:${size}px`}>
    ${src && !err
      ? html`<img src=${src} alt=${alt || name} loading="lazy" referrerpolicy="no-referrer" onError=${() => setErr(true)} />`
      : html`<span class="ph" style=${`font-size:${Math.round(size * 0.42)}px`} aria-label=${alt || name}>${initial}</span>`}
    ${status !== undefined && status !== null ? html`<span class=${`status ${cls}`}></span>` : null}
  </div>`;
}

export const Level = ({ level, lg }) =>
  html`<span class=${`level ${lg ? "lg" : ""} ${level >= 100 ? "wide" : ""}`} style=${`--c:${levelColor(level)}`} title=${level == null ? "Level unknown" : `Steam level ${level}`}>${level ?? "?"}</span>`;

export const Loader = ({ size = 40, contained, onColor }) =>
  html`<span class=${`loader ${contained ? "contained" : ""} ${onColor ? "on-color" : ""}`} style=${`--s:${size}px`} role="progressbar" aria-label="Loading"><i></i></span>`;

export const Check = ({ on, mixed, onClick, label }) => html`
  <button class=${`check ${on ? "on" : ""} ${mixed && !on ? "mixed" : ""}`} role="checkbox" aria-checked=${on ? "true" : mixed ? "mixed" : "false"}
    aria-label=${label} onClick=${(e) => { e.stopPropagation(); onClick && onClick(e); }}>
    <${Icon} name=${mixed && !on ? "indeterminate_check_box" : "check"} />
  </button>`;

export const Switch = ({ on, onChange, label }) =>
  html`<button class=${`switch ${on ? "on" : ""}`} role="switch" aria-checked=${on ? "true" : "false"} aria-label=${label} onClick=${() => onChange(!on)}></button>`;

export const Tag = ({ icon, kind = "", children, title }) =>
  html`<span class=${`tag ${kind}`} title=${title}>${icon ? html`<${Icon} name=${icon} fill />` : null}${children}</span>`;

export function Empty({ icon = "inventory_2", title, children }) {
  return html`<div class="empty">
    <div class="art"><${Icon} name=${icon} size="xl" /></div>
    <div class="headline-s" style="color:var(--on-surface)">${title}</div>
    <div class="body-m" style="max-width:420px">${children}</div>
  </div>`;
}

/** Render children into <body> so fixed overlays escape transformed / clipped ancestors. */
export function Portal({ children }) {
  const host = useRef(null);
  if (!host.current) {
    host.current = document.createElement("div");
    document.body.appendChild(host.current);
  }
  useLayoutEffect(() => render(children, host.current));
  useEffect(() => () => {
    render(null, host.current);
    host.current.remove();
  }, []);
  return null;
}

// ------------------------------------------------------------------ menu
export function MenuButton({ icon = "more_vert", items, label = "More", class: cls = "icon-btn", children }) {
  const [pos, setPos] = useState(null);
  const btn = useRef();
  const open = (e) => {
    e.stopPropagation();
    const r = btn.current.getBoundingClientRect();
    setPos({ top: r.bottom + 4, right: Math.max(8, window.innerWidth - r.right), bottomSpace: window.innerHeight - r.bottom });
  };
  return html`
    <button ref=${btn} class=${cls} aria-label=${label} aria-haspopup="menu" onClick=${open}>${children || html`<${Icon} name=${icon} />`}</button>
    ${pos ? html`<${Portal}><${Menu} pos=${pos} items=${items} onClose=${() => setPos(null)} /><//>` : null}`;
}

function Menu({ pos, items, onClose }) {
  const ref = useRef();
  const [style, setStyle] = useState(`top:${pos.top}px;right:${pos.right}px`);
  useLayoutEffect(() => {
    const h = ref.current.offsetHeight;
    if (h > pos.bottomSpace - 12) setStyle(`bottom:${window.innerHeight - pos.top + 48}px;right:${pos.right}px;transform-origin:bottom right`);
  }, []);
  useEffect(() => {
    const close = (e) => {
      if (e.type === "keydown" && e.key !== "Escape") return;
      if (e.type === "pointerdown" && ref.current && ref.current.contains(e.target)) return;
      onClose();
    };
    setTimeout(() => {
      document.addEventListener("pointerdown", close);
      document.addEventListener("keydown", close);
      window.addEventListener("resize", onClose);
    });
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
      window.removeEventListener("resize", onClose);
    };
  }, []);
  return html`<div class="menu glass-strong" role="menu" ref=${ref} style=${style}>
    ${items.filter(Boolean).map((it, i) =>
      it === "sep"
        ? html`<hr key=${i} />`
        : it.href
        ? html`<a key=${i} role="menuitem" href=${it.href} target="_blank" rel="noreferrer noopener" onClick=${onClose}><${Icon} name=${it.icon} />${it.label}</a>`
        : html`<button key=${i} role="menuitem" class=${it.danger ? "danger" : ""} onClick=${(e) => { e.stopPropagation(); onClose(); it.onClick(); }}>
            <${Icon} name=${it.icon} />${it.label}</button>`)}
  </div>`;
}

// ------------------------------------------------------------------ overlays
function useEscape(onClose) {
  useEffect(() => {
    const h = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [onClose]);
}

export function Dialog({ title, icon, onClose, children, footer, width }) {
  useEscape(onClose);
  return html`<div class="scrim" onClick=${onClose}></div>
    <div class="dialog glass-strong" role="dialog" aria-modal="true" aria-label=${title} style=${width ? `--w:${width}px` : ""}>
      <header>
        ${icon ? html`<${Icon} name=${icon} />` : null}
        <h2 class="headline-s">${title}</h2>
        <button class="icon-btn" aria-label="Close" onClick=${onClose}><${Icon} name="close" /></button>
      </header>
      <div class="body">${children}</div>
      ${footer ? html`<footer>${footer}</footer>` : null}
    </div>`;
}

export function Sheet({ onClose, children, label }) {
  useEscape(onClose);
  return html`<div class="scrim" onClick=${onClose}></div>
    <aside class="sheet" role="dialog" aria-modal="true" aria-label=${label}>${children}</aside>`;
}

export function Toasts() {
  return html`<div class="toasts" aria-live="polite">
    ${toasts.value.map((t) => html`<div key=${t.id} class=${`toast ${t.kind === "error" ? "error" : ""}`}>
      ${t.kind === "error" ? html`<${Icon} name="error" fill />` : null}
      <span class="grow">${t.text}</span>
      ${t.action ? html`<button class="btn text sm" onClick=${() => { t.action.onClick(); dismissToast(t.id); }}>${t.action.label}</button>` : null}
      <button class="icon-btn sm" aria-label="Dismiss" style="color:inherit" onClick=${() => dismissToast(t.id)}><${Icon} name="close" /></button>
    </div>`)}
  </div>`;
}

export function Lightbox() {
  const src = lightbox.value;
  useEffect(() => {
    if (!src) return;
    const h = (e) => e.key === "Escape" && (lightbox.value = null);
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [src]);
  if (!src) return null;
  return html`<div class="lightbox" onClick=${() => (lightbox.value = null)}><img src=${src} alt="" /></div>`;
}

/** Async button: shows a loader while the promise runs. */
export function AsyncButton({ onClick, class: cls = "btn", children, disabled, title, label }) {
  const [busy, setBusy] = useState(false);
  const run = async (e) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    try {
      await onClick(e);
    } finally {
      setBusy(false);
    }
  };
  return html`<button class=${`${cls} ${busy ? "busy" : ""}`} disabled=${disabled} aria-busy=${busy ? "true" : "false"} onClick=${run} title=${title} aria-label=${label}>
    ${busy ? html`<${Loader} size=${22} onColor />` : null}${children}</button>`;
}
