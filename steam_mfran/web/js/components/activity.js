import { html, useState } from "../../vendor/preact.js";
import { history, broadcasts } from "../store.js";
import { money, ago } from "../format.js";
import { Icon, Empty } from "./ui.js";
import { JobProgress } from "./dialogs.js";

const ICON = { accept: "how_to_reg", ignore: "do_not_disturb_on", block: "block" };
const VERB = { accept: "Accepted", ignore: "Ignored", block: "Blocked" };

export function ActivityPage() {
  const [filter, setFilter] = useState("all");
  const items = [...history.value].reverse().filter((h) => filter === "all" || h.action === filter || (filter === "auto" && h.source !== "manual"));
  const jobs = [...broadcasts.value.values()].sort((a, b) => b.created_at - a.created_at);
  const counts = history.value.reduce((acc, h) => ((acc[h.action] = (acc[h.action] || 0) + (h.ok ? 1 : 0)), acc), {});
  return html`
    <div class="summary-strip">
      <div class="stat"><span class="k">Accepted</span><span class="v">${counts.accept || 0}</span><${Icon} name="how_to_reg" class="bg" /></div>
      <div class="stat"><span class="k">Ignored</span><span class="v">${counts.ignore || 0}</span><${Icon} name="do_not_disturb_on" class="bg" /></div>
      <div class="stat"><span class="k">Blocked</span><span class="v">${counts.block || 0}</span><${Icon} name="block" class="bg" /></div>
      <div class="stat"><span class="k">Broadcasts</span><span class="v">${jobs.length}</span><${Icon} name="campaign" class="bg" /></div>
    </div>
    ${jobs.length ? html`<h2 class="title-l" style="margin:8px 4px 12px">Broadcasts</h2>
      <div class="grid" style="margin-bottom:24px">${jobs.slice(0, 6).map((j) => html`<div class="section" key=${j.id}>
        <div class="row" style="margin-bottom:8px"><${Icon} name="campaign" /><span class="title-s grow ellipsis">“${j.text}”</span><span class="body-s muted">${ago(j.created_at)}</span></div>
        <${JobProgress} job=${j} /></div>`)}</div>` : null}
    <div class="row" style="margin:8px 4px 12px"><h2 class="title-l grow" style="margin:0">History</h2>
      <div class="segmented sm">${[["all", "All"], ["accept", "Accepted"], ["ignore", "Ignored"], ["block", "Blocked"], ["auto", "By rules"]].map(([v, l]) =>
        html`<button class=${filter === v ? "on" : ""} onClick=${() => setFilter(v)}>${l}</button>`)}</div></div>
    ${items.length === 0 ? html`<${Empty} icon="history" title="Nothing here yet">Every accept, ignore and block — manual or automatic — is logged here.<//>`
      : html`<div class="section" style="padding:8px">${items.slice(0, 400).map((h, i) => html`<div class="history-row" key=${i}>
          <div class=${`ico ${h.action}`}><${Icon} name=${ICON[h.action]} /></div>
          <div class="col" style="gap:0;min-width:0">
            <span class="title-s ellipsis"><a href=${`https://steamcommunity.com/profiles/${h.steamid}/`} target="_blank" rel="noreferrer" style="color:inherit">${h.name || h.steamid}</a></span>
            <span class="body-s muted ellipsis">${h.ok ? VERB[h.action] : `Failed to ${h.action}: ${h.error}`}${h.source !== "manual" ? ` · ${h.source}` : ""}
              ${h.level != null ? ` · level ${h.level}` : ""}${h.value != null ? ` · ${money(h.value)}` : ""}</span>
          </div>
          <span class="body-s muted" title=${new Date(h.ts * 1000).toLocaleString()}>${ago(h.ts)}</span>
        </div>`)}</div>`}
  `;
}
