import { html, useState, useEffect } from "../../vendor/preact.js";
import { settings, prices, providers, currency, auth, demo, version, personas, toast, toastError } from "../store.js";
import { put, post } from "../api.js";
import { ago } from "../format.js";
import { SWATCHES } from "../theme.js";
import { Icon, Switch, AsyncButton, Avatar } from "./ui.js";

const CURRENCIES = ["USD", "EUR", "GBP", "RUB", "UAH", "KZT", "PLN", "TRY", "BRL", "CNY", "CAD", "AUD", "JPY", "INR", "SEK", "NOK", "CHF", "CZK"];

const save = (patch) => put("/settings", patch).catch(toastError);

function Setting({ icon, title, desc, children }) {
  return html`<div class="setting">${icon ? html`<${Icon} name=${icon} />` : null}
    <div class="txt"><div class="title-s">${title}</div>${desc ? html`<div class="body-s muted">${desc}</div>` : null}</div>${children}</div>`;
}

function NumberSetting({ value, min, max, step = 1, suffix, onSave }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return html`<label class="field compact" style="width:150px">
    <input type="number" min=${min} max=${max} step=${step} value=${v} onInput=${(e) => setV(e.target.value)}
      onBlur=${() => v !== "" && Number(v) !== value && onSave(Number(v))} onKeyDown=${(e) => e.key === "Enter" && e.target.blur()} />
    <span class="body-s muted" style="padding-right:10px">${suffix}</span></label>`;
}

export function SettingsPage() {
  const s = settings.value;
  const pr = prices.value || {};
  const a = auth.value || {};
  const me = personas.value.get(a.steamid) || {};
  const [notif, setNotif] = useState(typeof Notification !== "undefined" ? Notification.permission : "unsupported");
  const askNotif = async () => setNotif(await Notification.requestPermission());
  return html`<div style="max-width:880px">
    <section class="section">
      <h3 class="title-l">Appearance</h3>
      <${Setting} icon="dark_mode" title="Theme"><div class="segmented sm">
        ${[["system", "System", "brightness_auto"], ["light", "Light", "light_mode"], ["dark", "Dark", "dark_mode"]].map(([v, l, ic]) =>
          html`<button class=${s.theme === v ? "on" : ""} onClick=${() => save({ theme: v })}><${Icon} name=${ic} />${l}</button>`)}</div><//>
      <${Setting} icon="palette" title="Colour style" desc="How strongly the accent colour tints the interface."><div class="segmented sm">
        ${[["tonal", "Calm"], ["vibrant", "Vibrant"], ["expressive", "Expressive"]].map(([v, l]) =>
          html`<button class=${s.palette === v ? "on" : ""} onClick=${() => save({ palette: v })}>${l}</button>`)}</div><//>
      <div class="setting" style="flex-direction:column;align-items:stretch">
        <div class="row"><${Icon} name="palette" /><div class="txt"><div class="title-s">Accent colour</div>
          <div class="body-s muted">Material You builds the whole palette from this one colour.</div></div></div>
        <div class="swatches" style="padding-left:36px">
          <button class=${`swatch ${!s.accent ? "on" : ""}`} title="From my Steam avatar" style="background:conic-gradient(from 90deg,#1a9fff,#c252c9,#e8710a,#7cb342,#1a9fff)" onClick=${() => save({ accent: null })}>
            <${Icon} name="account_circle" fill /></button>
          ${SWATCHES.map((c) => html`<button class=${`swatch ${s.accent === c ? "on" : ""}`} style=${`background:${c}`} title=${c} onClick=${() => save({ accent: c })}>
            ${s.accent === c ? html`<${Icon} name="check" />` : null}</button>`)}
          <label class="swatch" title="Custom colour" style="background:var(--surface-container-highest);color:var(--on-surface)"><${Icon} name="add" />
            <input type="color" value=${s.accent || "#1a9fff"} style="position:absolute;opacity:0;width:0;height:0" onChange=${(e) => save({ accent: e.target.value })} /></label>
        </div>
      </div>
    </section>

    <section class="section">
      <h3 class="title-l">Inventory prices</h3>
      <p class="body-s muted" style="margin:0 0 12px">Values are estimates from public price lists — good for sorting requests, not for appraising items.</p>
      <div class="col" style="gap:8px">${providers.value.map((p) => html`<button class=${`radio-card ${s.price_provider === p.id ? "on" : ""}`} onClick=${() => save({ price_provider: p.id })}>
        <${Icon} name=${s.price_provider === p.id ? "radio_button_checked" : "radio_button_unchecked"} />
        <div class="col" style="gap:2px"><span class="title-s">${p.label}</span><span class="body-s" style="opacity:.8">${p.description}</span></div></button>`)}</div>
      <${Setting} icon="sell" title="Price list status" desc=${pr.error ? html`<span style="color:var(--error)">${pr.error}</span>` : `${pr.count || 0} items · updated ${pr.updated_at ? ago(pr.updated_at) : "never"}`}>
        <${AsyncButton} class="btn tonal sm" onClick=${() => post("/prices/refresh").then(() => toast("Prices refreshed")).catch(toastError)}><${Icon} name="refresh" />Refresh<//><//>
      <${Setting} icon="currency_exchange" title="Display currency" desc=${currency.value.requested && currency.value.requested !== currency.value.code ? `Exchange rate for ${currency.value.requested} unavailable — showing USD.` : "Converted from USD with daily exchange rates."}>
        <label class="field compact" style="width:130px"><select value=${s.currency} onChange=${(e) => save({ currency: e.target.value })}>
          ${CURRENCIES.map((c) => html`<option value=${c}>${c}</option>`)}</select></label><//>
    </section>

    <section class="section">
      <h3 class="title-l">Friend requests</h3>
      <${Setting} icon="search" title="Analyse new requests automatically" desc="Load profile and CS2 inventory as soon as a request arrives.">
        <${Switch} on=${s.auto_analyze} label="Auto analyse" onChange=${(v) => save({ auto_analyze: v })} /><//>
      <${Setting} icon="refresh" title="Check for new requests every" desc="New invites also arrive instantly while chat is connected.">
        <${NumberSetting} value=${s.invites_poll_seconds} min="20" suffix="sec" onSave=${(v) => save({ invites_poll_seconds: v })} /><//>
      <${Setting} icon="speed" title="Pause between inventory requests" desc="Steam rate-limits inventory lookups. Lower is faster but risks temporary blocks.">
        <${NumberSetting} value=${s.inventory_interval_seconds} min="1" step="0.5" suffix="sec" onSave=${(v) => save({ inventory_interval_seconds: v })} /><//>
      <${Setting} icon="schedule" title="Reuse inventory results for" desc="Re-analysing within this window uses the cached value.">
        <${NumberSetting} value=${s.inventory_cache_hours} min="0" step="0.5" suffix="hours" onSave=${(v) => save({ inventory_cache_hours: v })} /><//>
    </section>

    <section class="section">
      <h3 class="title-l">Messages</h3>
      <${Setting} icon="campaign" title="Pause between broadcast messages" desc="Sending too fast can trigger Steam’s spam protection.">
        <${NumberSetting} value=${s.broadcast_interval_seconds} min="0.5" step="0.5" suffix="sec" onSave=${(v) => save({ broadcast_interval_seconds: v })} /><//>
      <${Setting} icon="notifications" title="Desktop notifications" desc="Get notified about new messages while MFRAN is in the background.">
        ${notif === "granted" ? html`<span class="tag good"><${Icon} name="check" />Enabled</span>`
          : notif === "unsupported" ? html`<span class="tag">Not supported</span>`
          : html`<button class="btn tonal sm" onClick=${askNotif}>${notif === "denied" ? "Blocked in browser" : "Enable"}</button>`}<//>
    </section>

    <section class="section">
      <h3 class="title-l">Account</h3>
      <div class="setting">
        <${Avatar} src=${me.avatar} size=${48} round name=${me.name || a.account_name} />
        <div class="txt"><div class="title-m">${me.name || a.account_name}</div>
          <div class="body-s muted">${a.account_name} · SteamID ${a.steamid}${a.session_expires_at ? ` · session valid until ${new Date(a.session_expires_at * 1000).toLocaleDateString()}` : ""}</div></div>
        <button class="btn danger" onClick=${() => post("/auth/logout").catch(toastError)}><${Icon} name="logout" />Sign out</button>
      </div>
      <p class="body-s muted" style="margin:4px 0 0">Signing out ends this Steam web session and deletes the saved refresh token. Your next sign-in needs the password and a Steam Guard code again.</p>
    </section>
    <p class="body-s muted" style="text-align:center;margin:24px 0">MFRAN ${version.value}${demo.value ? " · demo mode" : ""} · not affiliated with Valve</p>
  </div>`;
}
