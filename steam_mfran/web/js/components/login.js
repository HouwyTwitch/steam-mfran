import { html, useState, useRef, useEffect } from "../../vendor/preact.js";
import { auth, demo, toastError } from "../store.js";
import { post } from "../api.js";
import { Icon, BrandMark } from "../icons.js";
import { Loader, AsyncButton } from "./ui.js";

export function LoginScreen() {
  const a = auth.value || { state: "starting" };
  return html`<div class="login-wrap">
    <div class="login glass-strong">
      <div class="brand-xl"><${BrandMark} lg />
        <div><div class="title-l">MFRAN</div><div class="body-s muted">Steam friend requests, sorted.</div></div>
      </div>
      ${a.state === "starting" ? html`<div class="row" style="justify-content:center;padding:32px"><${Loader} size=${56} contained /></div>`
        : a.state === "awaiting_guard" ? html`<${GuardStep} a=${a} />`
        : a.state === "error" ? html`<${ErrorStep} a=${a} />`
        : html`<${CredentialsStep} a=${a} />`}
    </div>
  </div>`;
}

function CredentialsStep({ a }) {
  const [user, setUser] = useState(a.account_name || "");
  const [pass, setPass] = useState("");
  const [show, setShow] = useState(false);
  const busy = a.state === "authenticating";
  const submit = async (e) => {
    e && e.preventDefault();
    if (!user || !pass || busy) return;
    try {
      await post("/auth/login", { username: user.trim(), password: pass });
      setPass("");
    } catch (err) {
      toastError(err);
    }
  };
  return html`<form class="col" style="gap:14px" onSubmit=${submit}>
    <div>
      <h1 class="headline-m">${a.state === "expired" ? "Welcome back" : "Sign in to Steam"}</h1>
      <p class="body-m muted" style="margin-top:6px">
        ${a.state === "expired"
          ? "Your saved session is no longer valid. Sign in once more and MFRAN will keep you logged in."
          : "You only need your password and a Steam Guard code the first time. After that MFRAN reuses a refresh token."}
      </p>
    </div>
    ${a.error ? html`<div class="note" style="background:var(--error-container);color:var(--on-error-container)"><${Icon} name="error" fill /><span class="body-m">${a.error}</span></div>` : null}
    <label class="field labeled"><label>Account name</label>
      <input value=${user} onInput=${(e) => setUser(e.target.value)} autocomplete="username" autocapitalize="off" spellcheck="false" required autofocus=${!user} />
      <${Icon} name="person" />
    </label>
    <label class="field labeled"><label>Password</label>
      <input type=${show ? "text" : "password"} value=${pass} onInput=${(e) => setPass(e.target.value)} autocomplete="current-password" required autofocus=${!!user} />
      <button type="button" class="icon-btn sm" aria-label=${show ? "Hide password" : "Show password"} onClick=${() => setShow(!show)}><${Icon} name=${show ? "visibility_off" : "visibility"} /></button>
    </label>
    <button class="btn lg block" type="submit" disabled=${!user || !pass} aria-busy=${busy}>
      ${busy ? html`<${Loader} size=${24} onColor />` : html`<${Icon} name="key" />`} ${busy ? "Contacting Steam…" : "Sign in"}
    </button>
    <div class="note"><${Icon} name="lock" fill />
      <span class="body-s muted">Your password goes straight to Steam (RSA-encrypted) and is never saved. MFRAN stores only the
      Steam refresh token on this computer${demo.value ? "" : " in ./data/session.json"}.</span>
    </div>
    ${demo.value ? html`<div class="note"><${Icon} name="info" fill /><span class="body-s">Demo mode: any account name and password work, and any 5-character code.</span></div>` : null}
  </form>`;
}

function CodeBoxes({ onComplete, error }) {
  const [vals, setVals] = useState(["", "", "", "", ""]);
  const refs = [useRef(), useRef(), useRef(), useRef(), useRef()];
  useEffect(() => refs[0].current && refs[0].current.focus(), []);
  useEffect(() => {
    if (error) {
      setVals(["", "", "", "", ""]);
      refs[0].current && refs[0].current.focus();
    }
  }, [error]);
  const set = (next) => {
    setVals(next);
    if (next.every(Boolean)) onComplete(next.join(""));
  };
  const onInput = (i, e) => {
    const v = e.target.value.replace(/[^a-z0-9]/gi, "").toUpperCase();
    if (v.length > 1) return onPaste(i, v);
    const next = [...vals];
    next[i] = v;
    set(next);
    if (v && i < 4) refs[i + 1].current.focus();
  };
  const onPaste = (i, text) => {
    const chars = text.replace(/[^a-z0-9]/gi, "").toUpperCase().slice(0, 5 - i).split("");
    const next = [...vals];
    chars.forEach((c, k) => (next[i + k] = c));
    set(next);
    refs[Math.min(4, i + chars.length)].current.focus();
  };
  const onKey = (i, e) => {
    if (e.key === "Backspace" && !vals[i] && i > 0) refs[i - 1].current.focus();
    if (e.key === "ArrowLeft" && i > 0) refs[i - 1].current.focus();
    if (e.key === "ArrowRight" && i < 4) refs[i + 1].current.focus();
  };
  return html`<div class=${`code-boxes ${error ? "error" : ""}`}>
    ${vals.map((v, i) => html`<input key=${i} ref=${refs[i]} value=${v} inputmode="text" autocomplete="one-time-code" maxlength="5" aria-label=${`Code character ${i + 1}`}
      onInput=${(e) => onInput(i, e)} onKeyDown=${(e) => onKey(i, e)} onPaste=${(e) => { e.preventDefault(); onPaste(i, e.clipboardData.getData("text")); }} />`)}
  </div>`;
}

function GuardStep({ a }) {
  const g = a.guard || {};
  const [useCode, setUseCode] = useState(!g.confirmation);
  const [busy, setBusy] = useState(false);
  const [errTick, setErrTick] = useState(0);
  const submit = async (code) => {
    setBusy(true);
    try {
      await post("/auth/code", { code });
    } catch (e) {
      toastError(e);
      setErrTick((t) => t + 1);
    } finally {
      setBusy(false);
    }
  };
  const cancel = () => post("/auth/cancel").catch(toastError);
  const showCode = g.code && (useCode || !g.confirmation);
  return html`<div class="col" style="gap:16px">
    <div>
      <h1 class="headline-m">Steam Guard</h1>
      <p class="body-m muted" style="margin-top:6px">Signing in as <strong>${a.account_name}</strong>.</p>
    </div>
    ${g.confirmation && !showCode ? html`
      <div class="col" style="align-items:center;gap:14px;padding:8px 0">
        <div class="phone-art"><${Icon} name="mobile_check" size="lg" /></div>
        <div class="title-m" style="text-align:center">Approve this sign-in in the Steam Mobile app</div>
        <div class="row body-s muted"><${Loader} size=${20} /> Waiting for confirmation…</div>
      </div>
      ${g.code ? html`<button class="btn text" onClick=${() => setUseCode(true)}>Enter a code instead</button>` : null}` : null}
    ${showCode ? html`
      <p class="body-m" style="margin:0">${g.code === "email"
        ? html`Enter the code Steam e-mailed to you${g.hint ? html` at <strong>…@${g.hint}</strong>` : ""}.`
        : "Enter the 5-character code from the Steam Mobile app."}</p>
      <${CodeBoxes} onComplete=${submit} error=${errTick} />
      ${busy ? html`<div class="row body-s muted" style="justify-content:center"><${Loader} size=${20} /> Verifying…</div>` : null}
      ${a.error ? html`<div class="body-s" style="color:var(--error);text-align:center">${a.error}</div>` : null}
      ${g.confirmation ? html`<button class="btn text" onClick=${() => setUseCode(false)}>Approve in the app instead</button>` : null}` : null}
    <button class="btn outlined" onClick=${cancel}>Cancel</button>
  </div>`;
}

function ErrorStep({ a }) {
  return html`<div class="col" style="gap:16px">
    <h1 class="headline-m">Can’t reach Steam</h1>
    <p class="body-m muted">${a.error || "Steam did not respond."} Your saved session is kept; MFRAN retries automatically every minute.</p>
    <${AsyncButton} class="btn lg block" onClick=${() => post("/auth/retry").catch(toastError)}><${Icon} name="refresh" /> Retry now<//>
    <button class="btn outlined" onClick=${() => post("/auth/logout").catch(toastError)}>Sign in with a different account</button>
  </div>`;
}
