import { html, useState, useMemo } from "../../vendor/preact.js";
import { invites, invitesMeta, selection, detailId, dialog, settings, toast, toastError, go } from "../store.js";
import { post } from "../api.js";
import { money, accountAge, ageDays, ago, plural } from "../format.js";
import { Icon, Avatar, Level, Loader, Check, Tag, Empty, MenuButton, AsyncButton } from "./ui.js";

const FILTERS = [
  { id: "all", label: "All", icon: null, test: () => true },
  { id: "value", label: "Has value", icon: "payments", test: (r) => (r.inventory?.value || 0) >= 1 },
  { id: "private", label: "Private inventory", icon: "lock", test: (r) => r.inventory?.state === "private" },
  { id: "flagged", label: "Red flags", icon: "warning", test: (r) => flags(r).some((f) => f.kind === "error" || f.kind === "warn") },
  { id: "rule", label: "Matches a rule", icon: "bolt", test: (r) => !!r.rule },
  { id: "todo", label: "Not analysed", icon: "hourglass_top", test: (r) => r.analysis?.state !== "done" },
];

const SORTS = [
  { id: "newest", label: "Newest first", icon: "schedule", fn: (a, b) => b.first_seen - a.first_seen },
  { id: "value", label: "Inventory value", icon: "payments", fn: (a, b) => (b.inventory?.value ?? -1) - (a.inventory?.value ?? -1) },
  { id: "level", label: "Steam level", icon: "stars", fn: (a, b) => (b.level ?? -1) - (a.level ?? -1) },
  { id: "age", label: "Account age", icon: "cake", fn: (a, b) => (a.profile?.member_since ?? 9e12) - (b.profile?.member_since ?? 9e12) },
  { id: "name", label: "Name", icon: "sort", fn: (a, b) => (a.name || "").localeCompare(b.name || "") },
];

export function flags(r) {
  const p = r.profile || {};
  const out = [];
  if (p.vac_banned) out.push({ kind: "error", icon: "gpp_bad", label: "VAC ban" });
  if (p.trade_ban && p.trade_ban !== "None") out.push({ kind: "error", icon: "gavel", label: `Trade ${p.trade_ban.toLowerCase()}` });
  if (p.limited) out.push({ kind: "warn", icon: "person_off", label: "Limited" });
  const days = ageDays(p.member_since);
  if (days != null && days < 30) out.push({ kind: "warn", icon: "cake", label: "New account" });
  if (p.privacy && p.privacy !== "public") out.push({ kind: "", icon: "lock", label: p.privacy === "friendsonly" ? "Friends-only" : "Private profile" });
  if (r.rule) out.push({ kind: r.rule.action === "accept" ? "good" : "tertiary", icon: "bolt", label: `${r.rule.name} → ${r.rule.action}` });
  return out;
}

export const steamProfile = (r) => r.profile_url || `https://steamcommunity.com/profiles/${r.steamid}/`;
export const steamInventory = (r) => `https://steamcommunity.com/profiles/${r.steamid}/inventory/#730`;

export async function act(action, steamids, message) {
  if (!steamids.length) return;
  try {
    const res = await post("/invites/action", { action, steamids, message });
    const results = Object.values(res.results);
    const ok = results.filter((e) => e === null).length;
    const failed = results.length - ok;
    const verb = { accept: "Accepted", ignore: "Ignored", block: "Blocked" }[action];
    if (ok) toast(`${verb} ${plural(ok, "request")}${res.broadcast ? " · sending your message" : ""}`);
    if (failed) toast(`${plural(failed, "request")} failed: ${results.find((e) => e)}`, { kind: "error" });
    const s = new Set(selection.value);
    steamids.forEach((id) => s.delete(id));
    selection.value = s;
  } catch (e) {
    toastError(e);
  }
}

export function confirmBlock(ids) {
  dialog.value = {
    type: "confirm",
    props: {
      title: `Block ${plural(ids.length, "user")}?`,
      text: "Blocked users can’t message you or send new invites. You can unblock them later on Steam.",
      confirm: "Block",
      danger: true,
      onConfirm: () => act("block", ids),
    },
  };
}

export const acceptWithMessage = (ids) => (dialog.value = { type: "acceptMessage", props: { ids } });

const analyze = (ids, force = true) => post("/invites/analyze", { steamids: ids, force }).catch(toastError);

export function RequestsPage() {
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState(() => localStorage.getItem("mfran.sort") || "newest");
  const [q, setQ] = useState("");
  const [showHandled, setShowHandled] = useState(false);
  const all = [...invites.value.values()];
  const pending = all.filter((r) => r.status === "pending");
  const handled = all.filter((r) => r.status !== "pending").sort((a, b) => (b.acted_at || 0) - (a.acted_at || 0));
  const f = FILTERS.find((x) => x.id === filter) || FILTERS[0];
  const s = SORTS.find((x) => x.id === sort) || SORTS[0];
  const query = q.trim().toLowerCase();
  const list = useMemo(
    () => pending.filter((r) => f.test(r) && (!query || (r.name || "").toLowerCase().includes(query) || r.steamid.includes(query))).sort(s.fn),
    [invites.value, filter, sort, query],
  );
  const sel = selection.value;
  const allSelected = list.length > 0 && list.every((r) => sel.has(r.steamid));
  const someSelected = list.some((r) => sel.has(r.steamid));
  const toggleAll = () => {
    const next = new Set(sel);
    if (allSelected) list.forEach((r) => next.delete(r.steamid));
    else list.forEach((r) => next.add(r.steamid));
    selection.value = next;
  };
  const counts = Object.fromEntries(FILTERS.map((x) => [x.id, pending.filter(x.test).length]));
  const meta = invitesMeta.value;
  const analysed = pending.filter((r) => r.analysis?.state === "done").length;
  const totalValue = pending.reduce((a, r) => a + (r.inventory?.value || 0), 0);
  const flagged = counts.flagged;
  const setSortP = (id) => {
    setSort(id);
    localStorage.setItem("mfran.sort", id);
  };

  return html`
    <div class="summary-strip">
      <div class="stat accent"><span class="k">Pending requests</span><span class="v">${pending.length}</span><${Icon} name="person_add" class="bg" fill /></div>
      <div class="stat"><span class="k">Analysed</span><span class="v">${analysed}<span class="body-m muted"> / ${pending.length}</span></span>
        <div class="progress" style="margin-top:6px"><i style=${`width:${pending.length ? (analysed / pending.length) * 100 : 0}%`}></i></div></div>
      <div class="stat"><span class="k">Total inventory value</span><span class="v">${money(totalValue, { compact: true })}</span><${Icon} name="payments" class="bg" fill /></div>
      <div class="stat"><span class="k">With red flags</span><span class="v" style=${flagged ? "color:var(--error)" : ""}>${flagged}</span><${Icon} name="gpp_bad" class="bg" fill /></div>
    </div>

    <div class="req-toolbar">
      <div class="row1">
        <${Check} on=${allSelected} mixed=${someSelected} onClick=${toggleAll} label="Select all" />
        <label class="field search grow" style="max-width:420px">
          <${Icon} name="search" />
          <input placeholder="Search by name or SteamID" value=${q} onInput=${(e) => setQ(e.target.value)} />
          ${q ? html`<button class="icon-btn sm" aria-label="Clear search" onClick=${() => setQ("")}><${Icon} name="close" /></button>` : null}
        </label>
        <span class="spacer"></span>
        <${MenuButton} class="btn tonal sm" label="Sort" items=${SORTS.map((x) => ({ icon: x.id === sort ? "check" : x.icon, label: x.label, onClick: () => setSortP(x.id) }))}>
          <${Icon} name="sort" /> ${s.label}
        <//>
        <span class="body-s muted" title=${meta.error || ""}>${meta.error ? html`<span style="color:var(--error)">⚠ ${meta.error}</span>` : meta.last_refresh ? `Updated ${ago(meta.last_refresh)}` : "Loading…"}</span>
        <${AsyncButton} class="icon-btn tonal" label="Refresh friend requests" title="Refresh" onClick=${() => post("/invites/refresh").catch(toastError)}><${Icon} name="refresh" /><//>
      </div>
      <div class="chip-scroll">
        ${FILTERS.map((x) => html`<button class=${`chip ${filter === x.id ? "on" : ""}`} onClick=${() => setFilter(x.id)}>
          ${filter === x.id ? html`<${Icon} name="check" />` : x.icon ? html`<${Icon} name=${x.icon} />` : null}
          ${x.label}<span class="count">${counts[x.id]}</span></button>`)}
        ${handled.length ? html`<button class=${`chip ${showHandled ? "on" : ""}`} onClick=${() => setShowHandled(!showHandled)}>
          <${Icon} name=${showHandled ? "check" : "history"} /> Handled<span class="count">${handled.length}</span></button>` : null}
        ${meta.queue ? html`<span class="tag primary" style="height:32px"><${Loader} size=${18} /> ${meta.queue} queued</span>` : null}
      </div>
    </div>

    ${pending.length === 0 && !meta.last_refresh && !meta.error
      ? html`<div class="grid">${[0, 1, 2, 3, 4, 5].map((i) => html`<div key=${i} class="rcard"><div class="row"><div class="skeleton" style="width:56px;height:56px;border-radius:34%"></div>
          <div class="col grow"><div class="skeleton" style="height:16px;width:60%"></div><div class="skeleton" style="height:12px;width:40%"></div></div></div>
          <div class="skeleton" style="height:72px;border-radius:20px"></div><div class="skeleton" style="height:40px;border-radius:999px"></div></div>`)}</div>`
      : list.length === 0
      ? html`<${Empty} icon=${pending.length ? "filter_list" : "person_add"} title=${pending.length ? "Nothing matches this filter" : "No pending friend requests"}>
          ${pending.length ? "Try another filter or clear the search." : "New requests show up here automatically. MFRAN checks Steam in the background."}<//>`
      : html`<div class="grid">${list.map((r) => html`<${RequestCard} key=${r.steamid} r=${r} />`)}</div>`}

    ${showHandled && handled.length ? html`
      <div class="row" style="margin:28px 4px 12px"><h2 class="title-l" style="margin:0">Handled</h2><span class="spacer"></span>
        <button class="btn text sm" onClick=${() => post("/invites/clear").catch(toastError)}><${Icon} name="delete" /> Clear list</button></div>
      <div class="grid">${handled.map((r) => html`<${RequestCard} key=${r.steamid} r=${r} />`)}</div>` : null}

    ${sel.size ? html`<${FloatBar} ids=${[...sel]} />` : null}
  `;
}

function ValueBlock({ r }) {
  const a = r.analysis || {};
  const inv = r.inventory;
  if (r.status === "pending" && (a.state === "queued" || a.state === "running")) {
    return html`<div class="value"><div class="row"><${Loader} size=${36} />
      <div class="col" style="gap:0"><span class="title-s">${a.state === "queued" ? "Waiting in queue" : a.step === "inventory" ? "Pricing CS2 inventory…" : "Reading profile…"}</span>
      <span class="body-s muted">${a.state === "queued" ? "Analysis starts shortly" : "This takes a few seconds"}</span></div></div></div>`;
  }
  if (a.state === "waiting") {
    const secs = Math.max(0, Math.round((a.retry_at || 0) - Date.now() / 1000));
    return html`<div class="value"><div class="row"><${Icon} name="timer" /><div class="col" style="gap:0"><span class="title-s">Steam rate limit</span>
      <span class="body-s muted">Retrying ${secs ? `in ~${secs}s` : "soon"}</span></div></div></div>`;
  }
  if (a.state === "error" && !inv) {
    return html`<div class="value"><div class="row grow"><${Icon} name="error" /><div class="col grow" style="gap:0"><span class="title-s">Analysis failed</span>
      <span class="body-s muted ellipsis" title=${a.error}>${a.error}</span></div>
      <button class="btn text sm" onClick=${(e) => { e.stopPropagation(); analyze([r.steamid]); }}>Retry</button></div></div>`;
  }
  if (!inv) {
    return html`<div class="value"><div class="row grow"><span class="body-m muted grow">Not analysed yet</span>
      ${r.status === "pending" ? html`<button class="btn tonal sm" onClick=${(e) => { e.stopPropagation(); analyze([r.steamid], false); }}><${Icon} name="search" />Analyse</button>` : null}</div></div>`;
  }
  if (inv.state === "private") {
    return html`<div class="value"><div class="row"><${Icon} name="lock" fill /><div class="col" style="gap:0"><span class="title-m">Private inventory</span>
      <span class="body-s muted">Steam doesn’t show their items</span></div></div></div>`;
  }
  if (inv.state === "empty") {
    return html`<div class="value"><div class="col" style="gap:0"><span class="amount">${money(0)}</span><span class="body-s muted">No CS2 items</span></div></div>`;
  }
  if (inv.state === "error") {
    return html`<div class="value"><div class="row grow"><${Icon} name="error" /><span class="body-m grow">Inventory unavailable</span>
      <button class="btn text sm" onClick=${(e) => { e.stopPropagation(); analyze([r.steamid]); }}>Retry</button></div></div>`;
  }
  const big = (inv.value || 0) >= 100;
  return html`<div class="value">
    <div class="col" style="gap:0"><span class="label-s muted">CS2 INVENTORY</span><span class=${`amount ${big ? "big" : ""}`}>${money(inv.value)}</span></div>
    <div class="meta">${plural(inv.total_count, "item")}<br />${inv.priced_count < inv.total_count ? `${inv.priced_count} priced` : "all priced"}</div>
  </div>`;
}

function RequestCard({ r }) {
  const sel = selection.value.has(r.steamid);
  const selecting = selection.value.size > 0;
  const pending = r.status === "pending";
  const toggle = () => {
    if (!pending) return;
    const s = new Set(selection.value);
    sel ? s.delete(r.steamid) : s.add(r.steamid);
    selection.value = s;
  };
  const p = r.profile || {};
  const age = accountAge(p.member_since);
  const top = (r.inventory?.top_items || []).slice(0, 5);
  const fl = flags(r);
  const statusInfo = { accepted: ["check_circle", "Accepted", "good"], ignored: ["do_not_disturb_on", "Ignored", ""], blocked: ["block", "Blocked", "error"], gone: ["history", "No longer pending", ""] }[r.status];
  const menu = [
    pending && { icon: "chat", label: "Accept & send message…", onClick: () => acceptWithMessage([r.steamid]) },
    pending && { icon: "refresh", label: "Re-analyse", onClick: () => analyze([r.steamid]) },
    { icon: "info", label: "Details & inventory", onClick: () => (detailId.value = r.steamid) },
    "sep",
    { icon: "open_in_new", label: "Steam profile", href: steamProfile(r) },
    { icon: "backpack", label: "Steam inventory", href: steamInventory(r) },
    { icon: "content_copy", label: "Copy SteamID", onClick: () => navigator.clipboard.writeText(r.steamid).then(() => toast("SteamID copied")) },
    pending && "sep",
    pending && { icon: "block", label: "Block", danger: true, onClick: () => confirmBlock([r.steamid]) },
  ];
  return html`<article class=${`rcard ${sel ? "selected" : ""} ${pending ? "" : "handled"}`} onClick=${() => (selecting && pending ? toggle() : (detailId.value = r.steamid))}>
    ${r.busy ? html`<div class="state-overlay"><div class="col" style="align-items:center"><${Loader} size=${48} contained /><span class="label-l">${{ accept: "Accepting…", ignore: "Ignoring…", block: "Blocking…" }[r.busy]}</span></div></div>` : null}
    ${pending ? html`<div class="sel"><${Check} on=${sel} onClick=${toggle} label=${`Select ${r.name}`} /></div>` : null}
    <div class="head" style="padding-right:30px">
      <${Avatar} src=${r.avatar} size=${56} name=${r.name} />
      <div class="col grow" style="gap:3px">
        <div class="row"><span class="name ellipsis grow" title=${r.name}>${r.name || r.steamid}</span>
          <span class="when" title=${`Received ${new Date(r.first_seen * 1000).toLocaleString()}`}>${ago(r.first_seen)}</span></div>
        <div class="sub"><${Level} level=${r.level} />
          ${age ? html`<span title="Account age" style="flex:none">${age} on Steam</span>` : html`<span style="flex:none">Steam level</span>`}
          ${p.location ? html`<span class="ellipsis" title=${p.location}>· ${p.location.split(",").pop().trim()}</span>` : null}
        </div>
      </div>
    </div>
    <${ValueBlock} r=${r} />
    ${top.length ? html`<div class="items">${top.map((it) => html`<div class="thumb" style=${`--rc:#${it.rarity_color || "666"}`} title=${`${it.name}\n${money(it.price)}${it.count > 1 ? ` × ${it.count}` : ""}`}>
        <img src=${it.icon} alt="" loading="lazy" />${it.count > 1 ? html`<span class="cnt">×${it.count}</span>` : null}</div>`)}</div>` : null}
    ${fl.length ? html`<div class="flags">${fl.map((x) => html`<${Tag} kind=${x.kind} icon=${x.icon}>${x.label}<//>`)}</div>` : null}
    <div class="actions" onClick=${(e) => e.stopPropagation()}>
      ${pending
        ? html`<button class="btn primary-tonal" onClick=${() => act("accept", [r.steamid])}><${Icon} name="how_to_reg" />Accept</button>
          <button class="btn outlined" onClick=${() => act("ignore", [r.steamid])}><${Icon} name="close" />Ignore</button>`
        : html`<span class=${`tag ${statusInfo?.[2] || ""}`} style="height:32px"><${Icon} name=${statusInfo?.[0] || "info"} fill /> ${statusInfo?.[1]}</span>
          <span class="spacer"></span>
          ${r.status === "accepted" ? html`<button class="btn tonal sm" onClick=${() => go("chats", r.steamid)}><${Icon} name="chat" />Message</button>` : null}`}
      <${MenuButton} items=${menu} />
    </div>
    ${r.last_error ? html`<div class="body-s" style="color:var(--error)">⚠ ${r.last_error}</div>` : null}
  </article>`;
}

function FloatBar({ ids }) {
  const clear = () => (selection.value = new Set());
  return html`<div class="float-bar glass-strong" role="toolbar" aria-label="Bulk actions">
    <button class="icon-btn" aria-label="Clear selection" onClick=${clear}><${Icon} name="close" /></button>
    <span class="count">${ids.length} selected</span>
    <${AsyncButton} class="btn" onClick=${() => act("accept", ids)}><${Icon} name="how_to_reg" /><span class="lbl">Accept</span><//>
    <button class="btn tonal" onClick=${() => acceptWithMessage(ids)}><${Icon} name="chat" /><span class="lbl">Accept & message</span></button>
    <${AsyncButton} class="btn outlined" onClick=${() => act("ignore", ids)}><${Icon} name="close" /><span class="lbl">Ignore</span><//>
    <button class="btn danger" onClick=${() => confirmBlock(ids)}><${Icon} name="block" /><span class="lbl">Block</span></button>
    <button class="icon-btn" title="Re-analyse selected" aria-label="Re-analyse selected" onClick=${() => analyze(ids)}><${Icon} name="refresh" /></button>
  </div>`;
}
