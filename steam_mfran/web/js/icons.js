import { html } from "../vendor/preact.js";
import { ICONS } from "./icons-data.js";

export function Icon({ name, fill = false, size, class: cls = "" }) {
  const ic = ICONS[name];
  if (!ic) {
    console.warn("missing icon", name);
    return null;
  }
  const d = (fill && ic.f) || ic.d;
  return html`<svg class=${`ic ${size || ""} ${cls}`} viewBox="0 -960 960 960" aria-hidden="true"><path d=${d} /></svg>`;
}

export const BrandMark = ({ lg }) => html`
  <div class=${`brand-mark ${lg ? "lg" : ""}`} aria-hidden="true">
    <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="4.2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="17" cy="16" r="6.5" />
      <path d="M5.5 38c1.8-6.6 6.2-10 11.5-10 3.4 0 6.3 1.3 8.4 3.8" />
      <path d="M30 30.5l4.2 4.2 8.3-9.2" />
      <path d="M31 9.5h11M31 17h7" />
    </svg>
  </div>`;
