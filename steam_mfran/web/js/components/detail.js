import { html, useState, useEffect } from "../../vendor/preact.js";
import { invites, detailId, go } from "../store.js";
import { api } from "../api.js";
import { money, accountAge, ago } from "../format.js";
import { Icon, Avatar, Level, Loader, Tag, Sheet, MenuButton } from "./ui.js";
import { flags, act, confirmBlock, acceptWithMessage, steamProfile, steamInventory } from "./requests.js";

export function DetailSheet() {
  const id = detailId.value;
  const r = id ? invites.value.get(id) : null;
  if (!r) return null;
  return html`<${Sheet} label=${`Details for ${r.name}`} onClose=${() => (detailId.value = null)}><${Detail} key=${id} r=${r} /><//>`;
}

function Detail({ r }) {
  const [inv, setInv] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [q, setQ] = useState("");
  const load = async (refresh = false) => {
    setLoading(true);
    setErr(null);
    try {
      setInv(await api(`/invites/${r.steamid}/inventory${refresh ? "?refresh=true" : ""}`));
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    if (r.inventory?.state === "ok") load();
  }, []);
  const p = r.profile || {};
  const pending = r.status === "pending";
  const close = () => (detailId.value = null);
  const items = (inv?.items || []).filter((i) => !q || i.name.toLowerCase().includes(q.toLowerCase()));
  const fl = flags(r);
  return html`
    <div class="hero">
      <div class="row">
        <button class="icon-btn" aria-label="Close" onClick=${close}><${Icon} name="close" /></button>
        <span class="spacer"></span>
        <a class="icon-btn" href=${steamProfile(r)} target="_blank" rel="noreferrer" aria-label="Open Steam profile" title="Steam profile"><${Icon} name="open_in_new" /></a>
        <${MenuButton} items=${[
          { icon: "backpack", label: "Inventory on Steam", href: steamInventory(r) },
          { icon: "travel_explore", label: "CSFloat profile", href: `https://csfloat.com/profile/${r.steamid}` },
          { icon: "refresh", label: "Refresh inventory", onClick: () => load(true) },
        ]} />
      </div>
      <div class="row" style="gap:16px">
        <${Avatar} src=${r.avatar} size=${84} name=${r.name} />
        <div class="col grow" style="gap:4px">
          <div class="headline-s ellipsis">${r.name || r.steamid}</div>
          ${p.real_name || p.location ? html`<div class="body-m muted ellipsis">${[p.real_name, p.location].filter(Boolean).join(" · ")}</div>` : null}
          <div class="body-s muted">SteamID ${r.steamid}</div>
        </div>
      </div>
      <div class="kv-grid">
        <div class="kv"><div class="k">Level</div><div class="v"><${Level} level=${r.level} /></div></div>
        <div class="kv"><div class="k">Account age</div><div class="v">${accountAge(p.member_since) || "—"}</div></div>
        <div class="kv"><div class="k">Inventory</div><div class="v">${r.inventory?.state === "ok" || r.inventory?.state === "empty" ? money(r.inventory.value) : r.inventory?.state === "private" ? "Private" : "—"}</div></div>
        <div class="kv"><div class="k">Items</div><div class="v">${r.inventory?.total_count ?? "—"}</div></div>
      </div>
      ${fl.length ? html`<div class="row wrap">${fl.map((x) => html`<${Tag} kind=${x.kind} icon=${x.icon}>${x.label}<//>`)}</div>` : null}
      ${p.favorite_badge || p.in_game ? html`<div class="row wrap body-s muted">
        ${p.in_game ? html`<${Tag} icon="sports_esports">${p.in_game}<//>` : null}
        ${p.favorite_badge ? html`<${Tag} icon="workspace_premium">${p.favorite_badge}<//>` : null}</div>` : null}
      ${pending ? html`<div class="row">
        <button class="btn grow" onClick=${() => act("accept", [r.steamid])}><${Icon} name="how_to_reg" />Accept</button>
        <button class="btn tonal" onClick=${() => acceptWithMessage([r.steamid])}><${Icon} name="chat" />Accept & message</button>
        <button class="icon-btn outlined" aria-label="Ignore" title="Ignore" onClick=${() => act("ignore", [r.steamid]).then(() => (detailId.value = null))}><${Icon} name="close" /></button>
        <button class="icon-btn outlined" aria-label="Block" title="Block" style="color:var(--error)" onClick=${() => confirmBlock([r.steamid])}><${Icon} name="block" /></button>
      </div>` : r.status === "accepted" ? html`<button class="btn tonal" onClick=${() => { close(); go("chats", r.steamid); }}><${Icon} name="chat" />Open chat</button>` : null}
    </div>
    <div class="sheet-body">
      <div class="row" style="padding:12px 20px 8px">
        <h3 class="title-m" style="margin:0">CS2 inventory</h3>
        <span class="spacer"></span>
        ${inv ? html`<span class="body-s muted">fetched ${ago(inv.fetched_at)}</span>` : null}
      </div>
      ${r.inventory?.state === "private" ? html`<div class="body-m muted" style="padding:0 20px 20px">This inventory is private.</div>`
        : r.inventory?.state === "empty" ? html`<div class="body-m muted" style="padding:0 20px 20px">No CS2 items.</div>`
        : loading ? html`<div class="row" style="justify-content:center;padding:32px"><${Loader} size=${48} /></div>`
        : err ? html`<div class="row" style="padding:0 20px 20px"><span class="body-m" style="color:var(--error)">${err}</span><button class="btn text sm" onClick=${() => load(true)}>Retry</button></div>`
        : !inv ? html`<div class="row" style="padding:0 20px 20px"><span class="body-m muted grow">Not loaded.</span><button class="btn tonal sm" onClick=${() => load(true)}>Load inventory</button></div>`
        : html`
          ${inv.items.length > 8 ? html`<div style="padding:0 16px 8px"><label class="field compact"><${Icon} name="search" /><input placeholder="Filter items" value=${q} onInput=${(e) => setQ(e.target.value)} /></label></div>` : null}
          <div class="inv-list">${items.map((it) => html`<div class="inv-item" key=${it.market_hash_name}>
            <div class="thumb" style=${`--rc:#${it.rarity_color || "666"}`}><img src=${it.icon} alt="" loading="lazy" /></div>
            <div class="col" style="gap:2px;min-width:0">
              <a class="nm ellipsis" style=${it.name_color ? `color:#${it.name_color}` : "color:inherit"} href=${`https://steamcommunity.com/market/listings/730/${encodeURIComponent(it.market_hash_name)}`} target="_blank" rel="noreferrer" title=${it.market_hash_name}>${it.name}</a>
              <span class="body-s muted ellipsis">${[it.type, it.exterior].filter(Boolean).join(" · ")}${it.tradable ? "" : " · trade-locked"}</span>
            </div>
            <div class="pr">${it.price ? money(it.price * it.count) : html`<span class="muted body-s">no price</span>`}
              ${it.count > 1 ? html`<div class="body-s muted">${it.count} × ${money(it.price)}</div>` : null}</div>
          </div>`)}</div>`}
    </div>`;
}
