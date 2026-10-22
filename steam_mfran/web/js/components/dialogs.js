import { html, useState, useEffect } from "../../vendor/preact.js";
import { dialog, broadcasts, personas, go, toastError, toast, personaOf, rememberPersonas } from "../store.js";
import { api, post } from "../api.js";
import { plural } from "../format.js";
import { Icon, Avatar, Loader, Check, Dialog, AsyncButton } from "./ui.js";
import { act } from "./requests.js";

const close = () => (dialog.value = null);

export function Dialogs() {
  const d = dialog.value;
  if (!d) return null;
  const C = { broadcast: BroadcastDialog, acceptMessage: AcceptMessageDialog, newChat: NewChatDialog, confirm: ConfirmDialog }[d.type];
  return C ? html`<${C} ...${d.props} />` : null;
}

// ------------------------------------------------------------------ templates
const TPL_KEY = "mfran.templates";
const loadTemplates = () => {
  try {
    return JSON.parse(localStorage.getItem(TPL_KEY) || "[]");
  } catch {
    return [];
  }
};
function Templates({ text, setText }) {
  const [list, setList] = useState(loadTemplates);
  const save = () => {
    const t = text.trim();
    if (!t || list.includes(t)) return;
    const next = [t, ...list].slice(0, 12);
    setList(next);
    localStorage.setItem(TPL_KEY, JSON.stringify(next));
    toast("Saved as template");
  };
  const remove = (t) => {
    const next = list.filter((x) => x !== t);
    setList(next);
    localStorage.setItem(TPL_KEY, JSON.stringify(next));
  };
  return html`<div class="row wrap" style="gap:6px">
    ${list.map((t) => html`<span class="chip" style="padding-right:4px" key=${t}>
      <span class="ellipsis" style="max-width:220px;cursor:pointer" onClick=${() => setText(t)} title=${t}>${t}</span>
      <button class="icon-btn sm" style="width:24px;height:24px" aria-label="Delete template" onClick=${() => remove(t)}><${Icon} name="close" size="xs" /></button></span>`)}
    <button class="btn text sm" disabled=${!text.trim() || list.includes(text.trim())} onClick=${save}><${Icon} name="add" />Save as template</button>
  </div>`;
}

function MessageField({ text, setText, placeholder = "Write a message…" }) {
  return html`<label class="field" style="align-items:flex-start;min-height:120px">
    <textarea rows="4" style="min-height:96px" placeholder=${placeholder} value=${text} onInput=${(e) => setText(e.target.value)} maxlength="4000"></textarea>
  </label>
  <div class="row body-s muted" style="justify-content:space-between;margin:4px 4px 10px"><span>Sent exactly as written to every recipient.</span><span>${text.length}/4000</span></div>
  <${Templates} text=${text} setText=${setText} />`;
}

// ------------------------------------------------------------------ broadcast
export function JobProgress({ job }) {
  const done = job.sent + job.failed;
  const entries = Object.entries(job.results);
  return html`<div class="col" style="gap:10px">
    <div class="row"><span class="title-s grow">${job.state === "running" ? `Sending… ${done}/${job.total}` : job.state === "cancelled" ? "Cancelled" : "Finished"}</span>
      <span class="body-s muted">${job.sent} sent${job.failed ? ` · ${job.failed} failed` : ""}</span>
      ${job.state === "running" ? html`<button class="btn text sm" onClick=${() => post(`/broadcast/${job.id}/cancel`).catch(toastError)}>Stop</button>` : null}</div>
    <div class="progress"><i style=${`width:${(done / Math.max(1, job.total)) * 100}%`}></i></div>
    <div class="picker" style="max-height:220px">${entries.map(([sid, r]) => {
      const p = personas.value.get(sid) || personaOf(sid);
      return html`<div class="list-item" key=${sid}>
        <${Avatar} src=${p.avatar} size=${28} round name=${r.name || p.name} />
        <span class="grow ellipsis body-m">${r.name || p.name || sid}</span>
        ${r.state === "sending" ? html`<${Loader} size=${22} />`
          : r.state === "sent" ? html`<span style="color:var(--primary)"><${Icon} name="check_circle" fill size="sm" /></span>`
          : r.state === "failed" ? html`<span class="body-s" style="color:var(--error)" title=${r.error}>${r.error || "failed"}</span>`
          : html`<span class="body-s muted">${r.state}</span>`}
      </div>`;
    })}</div>
  </div>`;
}

function useFriends() {
  const [friends, setFriends] = useState(null);
  useEffect(() => {
    api("/chat/friends").then((list) => {
      rememberPersonas(list);
      setFriends(list);
    }).catch((e) => {
      toastError(e);
      setFriends([]);
    });
  }, []);
  return friends;
}

function FriendPicker({ friends, selected, setSelected, multi = true, onPick }) {
  const [q, setQ] = useState("");
  if (!friends) return html`<div class="row" style="justify-content:center;padding:24px"><${Loader} /></div>`;
  const list = friends.filter((f) => !q || (f.name || "").toLowerCase().includes(q.toLowerCase()) || f.steamid.includes(q));
  const toggle = (sid) => {
    if (!multi) return onPick(sid);
    const s = new Set(selected);
    s.has(sid) ? s.delete(sid) : s.add(sid);
    setSelected(s);
  };
  const allOn = list.length > 0 && list.every((f) => selected?.has(f.steamid));
  return html`<div class="col" style="gap:8px">
    <div class="row">
      <label class="field compact grow"><${Icon} name="search" /><input placeholder=${`Search ${friends.length} friends`} value=${q} onInput=${(e) => setQ(e.target.value)} /></label>
      ${multi ? html`<button class="btn text sm" onClick=${() => { const s = new Set(selected); list.forEach((f) => (allOn ? s.delete(f.steamid) : s.add(f.steamid))); setSelected(s); }}>${allOn ? "Clear" : "Select all"}</button>
        <button class="btn text sm" onClick=${() => { const s = new Set(selected); list.filter((f) => f.state > 0).forEach((f) => s.add(f.steamid)); setSelected(s); }}>Online</button>` : null}
    </div>
    <div class="picker">
      ${list.length === 0 ? html`<div class="body-m muted" style="padding:16px;text-align:center">No friends found.</div>` : null}
      ${list.map((f) => html`<div class="list-item" key=${f.steamid} onClick=${() => toggle(f.steamid)}>
        ${multi ? html`<${Check} on=${selected.has(f.steamid)} onClick=${() => toggle(f.steamid)} label=${f.name} />` : null}
        <${Avatar} src=${f.avatar} size=${36} status=${f.game ? "ingame" : f.state ?? null} round name=${f.name} />
        <div class="col grow" style="gap:0"><span class="title-s ellipsis">${f.name || f.steamid}</span>
          <span class="body-s muted ellipsis">${f.game ? `Playing ${f.game}` : f.state ? "Online" : "Offline"}</span></div>
      </div>`)}
    </div>
  </div>`;
}

function BroadcastDialog({ preselect = [], recipients }) {
  const friends = useFriends();
  const [selected, setSelected] = useState(() => new Set(preselect));
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState(null);
  const job = jobId ? broadcasts.value.get(jobId) : null;
  const send = async () => {
    try {
      const j = await post("/broadcast", { steamids: [...selected], text: text.trim() });
      setJobId(j.id);
    } catch (e) {
      toastError(e);
    }
  };
  return html`<${Dialog} title="Broadcast message" icon="campaign" onClose=${close} width=${600}
    footer=${job ? html`<button class="btn" onClick=${close}>${job.state === "running" ? "Hide" : "Done"}</button>`
      : html`<button class="btn text" onClick=${close}>Cancel</button>
        <${AsyncButton} class="btn" disabled=${!selected.size || !text.trim()} onClick=${send}><${Icon} name="send" />Send to ${plural(selected.size, "friend")}<//>`}>
    ${job ? html`<${JobProgress} job=${job} />` : html`
      <div class="col" style="gap:14px">
        <${MessageField} text=${text} setText=${setText} />
        <div class="title-s">Recipients ${selected.size ? html`<span class="muted">· ${selected.size} selected</span>` : null}</div>
        <${FriendPicker} friends=${friends} selected=${selected} setSelected=${setSelected} />
        <div class="body-s muted">Messages go out one by one with a short pause between them, so Steam doesn’t flag them as spam.</div>
      </div>`}
  <//>`;
}

function AcceptMessageDialog({ ids }) {
  const [text, setText] = useState("");
  const names = ids.slice(0, 3).map((id) => personaOf(id).name || id).join(", ") + (ids.length > 3 ? ` +${ids.length - 3}` : "");
  return html`<${Dialog} title=${`Accept ${plural(ids.length, "request")}`} icon="how_to_reg" onClose=${close}
    footer=${html`<button class="btn text" onClick=${close}>Cancel</button>
      <${AsyncButton} class="btn" onClick=${async () => { await act("accept", ids, text.trim() || undefined); close(); }}>
        <${Icon} name=${text.trim() ? "send" : "how_to_reg"} />${text.trim() ? "Accept & send" : "Accept"}<//>`}>
    <p class="body-m muted" style="margin:0 0 12px">${names}. After they become friends, MFRAN sends this message to each of them.</p>
    <${MessageField} text=${text} setText=${setText} placeholder="Hey! Thanks for the add 👋" />
  <//>`;
}

function NewChatDialog() {
  const friends = useFriends();
  return html`<${Dialog} title="New message" icon="edit" onClose=${close} width=${480}>
    <${FriendPicker} friends=${friends} multi=${false} onPick=${(sid) => { close(); go("chats", sid); }} />
  <//>`;
}

function ConfirmDialog({ title, text, confirm = "OK", danger, onConfirm }) {
  return html`<${Dialog} title=${title} onClose=${close} width=${440}
    footer=${html`<button class="btn text" onClick=${close}>Cancel</button>
      <${AsyncButton} class=${`btn ${danger ? "danger-filled" : ""}`} onClick=${async () => { await onConfirm(); close(); }}>${confirm}<//>`}>
    <p class="body-m" style="margin:0">${text}</p>
  <//>`;
}
