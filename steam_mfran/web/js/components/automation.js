import { html, useState } from "../../vendor/preact.js";
import { settings, invites, dialog, currency, toast, toastError } from "../store.js";
import { put, post } from "../api.js";
import { money, toDisplay, fromDisplay, plural } from "../format.js";
import { Icon, Switch, Dialog, AsyncButton, Empty } from "./ui.js";

const ACTIONS = {
  accept: { icon: "how_to_reg", label: "Accept", style: "background:color-mix(in oklab,#1e8e3e 22%,var(--surface-container-high));color:#1e8e3e" },
  ignore: { icon: "do_not_disturb_on", label: "Ignore", style: "background:var(--surface-container-highest);color:var(--on-surface-variant)" },
  block: { icon: "block", label: "Block", style: "background:var(--error-container);color:var(--on-error-container)" },
};

const PRESETS = [
  { name: "Ignore likely bots", action: "ignore", conditions: { level_max: 0, inventory: "private" } },
  { name: "Block VAC-banned accounts", action: "block", conditions: { vac_banned: "yes" } },
  { name: "Ignore fresh accounts", action: "ignore", conditions: { account_age_days_max: 30, level_max: 5 } },
  { name: "Accept valuable inventories", action: "accept", conditions: { value_min: 100, vac_banned: "no", trade_ban: "no" } },
];

const saveRules = (rules) => put("/settings", { rules }).catch(toastError);

export function describe(c) {
  const out = [];
  if (c.level_min != null || c.level_max != null)
    out.push(c.level_min != null && c.level_max != null ? `level ${c.level_min}–${c.level_max}` : c.level_min != null ? `level ≥ ${c.level_min}` : `level ≤ ${c.level_max}`);
  if (c.value_min != null) out.push(`value ≥ ${money(c.value_min)}`);
  if (c.value_max != null) out.push(`value ≤ ${money(c.value_max)}`);
  if (c.inventory && c.inventory !== "any") out.push(`${c.inventory} inventory`);
  const tri = (v, yes, no) => v === "yes" ? yes : v === "no" ? no : null;
  out.push(tri(c.vac_banned, "VAC banned", "no VAC ban"), tri(c.trade_ban, "trade banned", "no trade ban"), tri(c.limited, "limited", "not limited"),
    tri(c.profile_private, "private profile", "public profile"));
  if (c.account_age_days_min != null) out.push(`account ≥ ${c.account_age_days_min} d`);
  if (c.account_age_days_max != null) out.push(`account ≤ ${c.account_age_days_max} d`);
  const f = out.filter(Boolean);
  return f.length ? f.join(" · ") : "every analysed request";
}

export function AutomationPage() {
  const s = settings.value;
  const rules = s.rules || [];
  const matches = {};
  for (const r of invites.value.values()) if (r.status === "pending" && r.rule) matches[r.rule.id] = (matches[r.rule.id] || 0) + 1;
  const totalMatches = Object.values(matches).reduce((a, b) => a + b, 0);
  const move = (i, d) => {
    const next = [...rules];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    saveRules(next);
  };
  const edit = (rule, index) => (dialog.value = { type: "rule", props: { rule, index } });
  const runNow = async () => {
    try {
      const res = await post("/automation/run");
      toast(res.processed ? `Rules handled ${plural(res.processed, "request")}` : "No pending request matches a rule");
    } catch (e) {
      toastError(e);
    }
  };
  return html`
    <div class="section" style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
      <div class="rule-action" style=${`width:56px;height:56px;border-radius:18px;${s.automation_enabled ? "background:var(--primary);color:var(--on-primary)" : "background:var(--surface-container-highest)"}`}>
        <${Icon} name="bolt" fill size="lg" /></div>
      <div class="col grow" style="gap:2px;min-width:220px">
        <div class="title-l">Automatic actions</div>
        <div class="body-m muted">${s.automation_enabled
          ? "On — every request is checked against your rules as soon as its analysis finishes."
          : "Off — rules only highlight matching requests. Turn this on to act on them automatically."}</div>
      </div>
      <${AsyncButton} class="btn tonal" disabled=${!totalMatches} onClick=${runNow}><${Icon} name="play_arrow" />Run now on ${plural(totalMatches, "match", "matches")}<//>
      <${Switch} on=${s.automation_enabled} label="Automation" onChange=${(v) => put("/settings", { automation_enabled: v }).catch(toastError)} />
    </div>

    <div class="row" style="margin:28px 4px 12px"><h2 class="title-l" style="margin:0">Rules</h2><span class="body-s muted">· first matching rule wins</span>
      <span class="spacer"></span><button class="btn" onClick=${() => edit(null, -1)}><${Icon} name="add" />New rule</button></div>

    ${rules.length === 0 ? html`<div class="section"><${Empty} icon="rule" title="No rules yet">Rules decide what happens to a request after MFRAN analyses it. Start from a preset:<//>
        <div class="row wrap" style="justify-content:center;gap:8px;margin-top:-28px;padding-bottom:12px">
          ${PRESETS.map((p) => html`<button class="chip" onClick=${() => edit({ ...p, enabled: true, message: null }, -1)}><${Icon} name=${ACTIONS[p.action].icon} />${p.name}</button>`)}
        </div></div>`
      : rules.map((r, i) => html`<div class="rule" key=${r.id} style=${r.enabled ? "" : "opacity:.6"}>
        <div class="rule-action" style=${ACTIONS[r.action].style}><${Icon} name=${ACTIONS[r.action].icon} /></div>
        <div class="col grow" style="gap:4px">
          <div class="row"><span class="title-m">${r.name}</span><span class="tag">${ACTIONS[r.action].label}</span>
            ${matches[r.id] ? html`<span class="tag primary">${plural(matches[r.id], "pending match", "pending matches")}</span>` : null}</div>
          <div class="body-m muted">When ${describe(r.conditions)}</div>
          ${r.message ? html`<div class="body-s muted ellipsis">💬 then sends: “${r.message}”</div>` : null}
        </div>
        <div class="order">
          <button class="icon-btn sm" aria-label="Move up" disabled=${i === 0} onClick=${() => move(i, -1)}><${Icon} name="arrow_upward" /></button>
          <button class="icon-btn sm" aria-label="Move down" disabled=${i === rules.length - 1} onClick=${() => move(i, 1)}><${Icon} name="arrow_downward" /></button>
        </div>
        <button class="icon-btn" aria-label="Edit rule" onClick=${() => edit(r, i)}><${Icon} name="edit" /></button>
        <${Switch} on=${r.enabled} label="Rule enabled" onChange=${(v) => saveRules(rules.map((x, k) => (k === i ? { ...x, enabled: v } : x)))} />
      </div>`)}
    ${dialog.value?.type === "rule" ? html`<${RuleEditor} ...${dialog.value.props} />` : null}
  `;
}

const TRI = [["any", "Any"], ["yes", "Yes"], ["no", "No"]];

function Seg({ value, options, onChange }) {
  return html`<div class="segmented sm">${options.map(([v, l]) => html`<button class=${value === v ? "on" : ""} onClick=${() => onChange(v)}>${value === v ? html`<${Icon} name="check" />` : null}${l}</button>`)}</div>`;
}

function Num({ label, value, onChange, step = 1, suffix }) {
  // Keep the typed text locally so "12." isn't normalised away mid-typing.
  const [txt, setTxt] = useState(value ?? "");
  return html`<label class="field labeled" style="min-height:52px"><label>${label}</label>
    <input type="number" step=${step} min="0" value=${txt} placeholder="—"
      onInput=${(e) => { setTxt(e.target.value); onChange(e.target.value === "" || Number.isNaN(Number(e.target.value)) ? null : Number(e.target.value)); }} />
    ${suffix ? html`<span class="body-s muted">${suffix}</span>` : null}</label>`;
}

function RuleEditor({ rule, index }) {
  const s = settings.value;
  const [r, setR] = useState(() => ({ name: "New rule", action: "accept", enabled: true, message: null, ...(rule || {}), conditions: { ...(rule?.conditions || {}) } }));
  const c = r.conditions;
  const setC = (k, v) => setR({ ...r, conditions: { ...c, [k]: v } });
  const round2 = (v) => (v == null ? null : Math.round(v * 100) / 100);
  const close = () => (dialog.value = null);
  const save = async () => {
    const rules = [...(s.rules || [])];
    const clean = { ...r, message: r.action === "accept" && r.message?.trim() ? r.message.trim() : null };
    if (index >= 0) rules[index] = clean;
    else rules.push(clean);
    await saveRules(rules);
    close();
  };
  const remove = async () => {
    await saveRules((s.rules || []).filter((_, i) => i !== index));
    close();
  };
  const cur = currency.value.code;
  return html`<${Dialog} title=${index >= 0 ? "Edit rule" : "New rule"} icon="rule" onClose=${close} width=${640}
    footer=${html`${index >= 0 ? html`<button class="btn text" style="--fg:var(--error);margin-right:auto" onClick=${remove}><${Icon} name="delete" />Delete</button>` : null}
      <button class="btn text" onClick=${close}>Cancel</button><${AsyncButton} class="btn" onClick=${save}>Save rule<//>`}>
    <div class="col" style="gap:16px">
      <label class="field labeled"><label>Rule name</label><input value=${r.name} onInput=${(e) => setR({ ...r, name: e.target.value })} maxlength="60" /></label>
      <div class="col" style="gap:6px"><span class="label-l">Then</span>
        <${Seg} value=${r.action} options=${[["accept", "Accept"], ["ignore", "Ignore"], ["block", "Block"]]} onChange=${(v) => setR({ ...r, action: v })} /></div>
      <span class="label-l" style="margin-bottom:-8px">When all of these are true</span>
      <div class="form-grid">
        <${Num} label="Level at least" value=${c.level_min} onChange=${(v) => setC("level_min", v)} />
        <${Num} label="Level at most" value=${c.level_max} onChange=${(v) => setC("level_max", v)} />
        <${Num} label=${`Inventory value ≥ (${cur})`} step="0.01" value=${round2(toDisplay(c.value_min))} onChange=${(v) => setC("value_min", fromDisplay(v))} />
        <${Num} label=${`Inventory value ≤ (${cur})`} step="0.01" value=${round2(toDisplay(c.value_max))} onChange=${(v) => setC("value_max", fromDisplay(v))} />
        <${Num} label="Account age at least" value=${c.account_age_days_min} suffix="days" onChange=${(v) => setC("account_age_days_min", v)} />
        <${Num} label="Account age at most" value=${c.account_age_days_max} suffix="days" onChange=${(v) => setC("account_age_days_max", v)} />
      </div>
      <div class="col" style="gap:10px">
        <div class="row wrap"><span class="body-m grow">CS2 inventory</span><${Seg} value=${c.inventory || "any"} options=${[["any", "Any"], ["public", "Public"], ["private", "Private"], ["empty", "Empty"]]} onChange=${(v) => setC("inventory", v)} /></div>
        <div class="row wrap"><span class="body-m grow">VAC banned</span><${Seg} value=${c.vac_banned || "any"} options=${TRI} onChange=${(v) => setC("vac_banned", v)} /></div>
        <div class="row wrap"><span class="body-m grow">Trade banned / probation</span><${Seg} value=${c.trade_ban || "any"} options=${TRI} onChange=${(v) => setC("trade_ban", v)} /></div>
        <div class="row wrap"><span class="body-m grow">Limited account</span><${Seg} value=${c.limited || "any"} options=${TRI} onChange=${(v) => setC("limited", v)} /></div>
        <div class="row wrap"><span class="body-m grow">Private profile</span><${Seg} value=${c.profile_private || "any"} options=${TRI} onChange=${(v) => setC("profile_private", v)} /></div>
      </div>
      ${r.action === "accept" ? html`<label class="field labeled" style="align-items:flex-start"><label>Message to send after accepting (optional)</label>
        <textarea rows="2" style="padding-top:24px" value=${r.message || ""} onInput=${(e) => setR({ ...r, message: e.target.value })} placeholder="Hey, thanks for the add!"></textarea></label>` : null}
      <div class="body-s muted">Summary: <strong>${ACTIONS[r.action].label}</strong> when ${describe(c)}.</div>
    </div>
  <//>`;
}
