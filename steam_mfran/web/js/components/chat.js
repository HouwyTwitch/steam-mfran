import { html, useState, useEffect, useRef, useLayoutEffect, useMemo } from "../../vendor/preact.js";
import {
  conversations, conversationsLoaded, messages, typing, unread, personas, route, go, dialog, chatStatus,
  loadConversations, loadMessages, markRead, addPendingMessage, failPendingMessage, removeMessage, personaOf, stripBB, toastError, toast,
} from "../store.js";
import { api, post } from "../api.js";
import { shortTime, clock, dayLabel, ago } from "../format.js";
import { BBCode, isMediaOnly } from "../bbcode.js";
import { Icon, Avatar, Loader, Empty, MenuButton } from "./ui.js";

const STATE_LABEL = { 0: "Offline", 1: "Online", 2: "Busy", 3: "Away", 4: "Snooze", 5: "Looking to trade", 6: "Looking to play" };
const statusOf = (p) => (p.game ? "ingame" : p.state);

export function ChatPage() {
  const active = route.value.id;
  useEffect(() => {
    if (!conversationsLoaded.value) loadConversations();
  }, []);
  return html`<div class=${`chat-layout ${active ? "has-active" : ""}`}>
    <${ConversationList} active=${active} />
    ${active ? html`<${ChatPane} key=${active} partner=${active} />` : html`<div class="chat-pane"><${Empty} icon="forum" title="Pick a conversation">
      Your Steam chats live here. Start a new one, or send the same message to many friends at once with <strong>Broadcast</strong>.<//></div>`}
  </div>`;
}

function ConversationList({ active }) {
  const [q, setQ] = useState("");
  const list = conversations.value.filter((c) => {
    if (!q) return true;
    const p = personaOf(c.partner);
    return (p.name || "").toLowerCase().includes(q.toLowerCase());
  });
  const st = chatStatus.value;
  return html`<section class="chat-list">
    <div class="row" style="padding:16px 16px 8px">
      <h2 class="title-l grow" style="margin:0">Chats</h2>
      <button class="icon-btn tonal" title="Broadcast a message" aria-label="Broadcast" onClick=${() => (dialog.value = { type: "broadcast", props: {} })}><${Icon} name="campaign" /></button>
      <button class="icon-btn filled" title="New message" aria-label="New message" onClick=${() => (dialog.value = { type: "newChat", props: {} })}><${Icon} name="edit" /></button>
    </div>
    <div style="padding:0 12px 8px"><label class="field compact"><${Icon} name="search" /><input placeholder="Search chats" value=${q} onInput=${(e) => setQ(e.target.value)} /></label></div>
    ${st.state !== "online" && st.transport !== "demo" ? html`<div class="body-s muted" style="padding:0 18px 8px">
      ${st.state === "connecting" ? "Connecting to Steam chat…" : "Live chat offline — messages are polled every few seconds."}</div>` : null}
    <div class="list">
      ${!conversationsLoaded.value ? html`<div class="row" style="justify-content:center;padding:28px"><${Loader} /></div>`
        : list.length === 0 ? html`<div class="body-m muted" style="padding:24px 12px;text-align:center">No recent conversations.</div>`
        : list.map((c) => html`<${ConvItem} key=${c.partner} c=${c} active=${active === c.partner} />`)}
    </div>
  </section>`;
}

function ConvItem({ c, active }) {
  const p = personas.value.get(c.partner) || personaOf(c.partner);
  const n = unread.value.get(c.partner) || 0;
  const isTyping = (typing.value.get(c.partner) || 0) > Date.now();
  return html`<button class=${`conv ${active ? "active" : ""} ${n ? "unread" : ""}`} onClick=${() => go("chats", c.partner)}>
    <${Avatar} src=${p.avatar} size=${46} status=${statusOf(p) ?? null} round name=${p.name} />
    <div class="col grow" style="gap:1px">
      <div class="row"><span class="who ellipsis grow">${p.name || c.partner}</span><span class="time">${shortTime(c.last_message)}</span></div>
      <div class="row"><span class="preview ellipsis grow">${isTyping ? html`<em>typing…</em>` : c.preview ? `${c.preview_out ? "You: " : ""}${stripBB(c.preview)}` : p.game ? `Playing ${p.game}` : ""}</span>
        ${n ? html`<span class="badge">${n}</span>` : null}</div>
    </div>
  </button>`;
}

function groupMessages(items) {
  const out = [];
  let lastDay = null;
  items.forEach((m, i) => {
    const day = new Date(m.timestamp * 1000).toDateString();
    if (day !== lastDay) {
      out.push({ sep: dayLabel(m.timestamp), key: `sep-${day}` });
      lastDay = day;
    }
    const prev = items[i - 1];
    const next = items[i + 1];
    const near = (a, b) => a && b && a.outgoing === b.outgoing && Math.abs(a.timestamp - b.timestamp) < 300 && new Date(a.timestamp * 1000).toDateString() === new Date(b.timestamp * 1000).toDateString();
    out.push({ m, first: !near(prev, m), last: !near(m, next) });
  });
  return out;
}

function ChatPane({ partner }) {
  const thread = messages.value.get(partner);
  const p = personas.value.get(partner) || personaOf(partner);
  const isTyping = (typing.value.get(partner) || 0) > Date.now();
  const scroller = useRef();
  const stick = useRef(true);
  const [drag, setDrag] = useState(false);
  const [files, setFiles] = useState([]);

  useEffect(() => {
    if (!thread?.loaded) loadMessages(partner);
  }, [partner]);
  const count = thread?.items.length || 0;
  useEffect(() => {
    if (thread?.loaded && document.visibilityState === "visible") markRead(partner);
  }, [count, thread?.loaded]);
  useEffect(() => {
    const h = () => document.visibilityState === "visible" && markRead(partner);
    document.addEventListener("visibilitychange", h);
    return () => document.removeEventListener("visibilitychange", h);
  }, [partner]);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [count, isTyping]);

  const onScroll = (e) => {
    const el = e.currentTarget;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (el.scrollTop < 60 && thread?.more && !thread.loading) {
      const h = el.scrollHeight;
      loadMessages(partner, true).then(() => requestAnimationFrame(() => (el.scrollTop = el.scrollHeight - h + el.scrollTop)));
    }
  };
  const addFiles = (list) => {
    const imgs = [...list].filter((f) => /^image\/(png|jpe?g|gif|webp)$/.test(f.type));
    if (imgs.length < list.length) toast("Only PNG, JPEG, GIF and WebP images can be sent", { kind: "error" });
    setFiles((cur) => [...cur, ...imgs.map((f) => ({ file: f, url: URL.createObjectURL(f) }))].slice(0, 6));
  };
  const groups = useMemo(() => groupMessages(thread?.items || []), [thread?.items]);
  const statusText = p.game ? `Playing ${p.game}` : p.state != null ? STATE_LABEL[p.state] || "Online" : p.last_seen ? `Last online ${ago(p.last_seen)}` : "";

  return html`<section class="chat-pane"
      onDragOver=${(e) => { e.preventDefault(); setDrag(true); }} onDragLeave=${(e) => e.currentTarget === e.target && setDrag(false)}
      onDrop=${(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}>
    <header class="chat-head">
      <button class="icon-btn" aria-label="Back" onClick=${() => go("chats")}><${Icon} name="arrow_back" /></button>
      <${Avatar} src=${p.avatar} size=${42} status=${statusOf(p) ?? null} round name=${p.name} />
      <div class="col grow" style="gap:0"><span class="title-m ellipsis">${p.name || partner}</span><span class="body-s muted ellipsis">${isTyping ? "typing…" : statusText}</span></div>
      <a class="icon-btn" href=${`https://steamcommunity.com/profiles/${partner}/`} target="_blank" rel="noreferrer" title="Steam profile" aria-label="Steam profile"><${Icon} name="open_in_new" /></a>
      <${MenuButton} items=${[
        { icon: "campaign", label: "Broadcast to this & others…", onClick: () => (dialog.value = { type: "broadcast", props: { preselect: [partner] } }) },
        { icon: "backpack", label: "View inventory on Steam", href: `https://steamcommunity.com/profiles/${partner}/inventory/#730` },
        { icon: "refresh", label: "Reload history", onClick: () => { messages.value = new Map([...messages.value].filter(([k]) => k !== partner)); loadMessages(partner); } },
      ]} />
    </header>
    <div class="messages" ref=${scroller} onScroll=${onScroll}>
      ${thread?.loading && !thread?.items.length ? html`<div class="row" style="justify-content:center;padding:40px"><${Loader} size=${48} /></div>` : null}
      ${thread?.more && thread?.items.length ? html`<div class="row" style="justify-content:center;padding:6px">${thread.loading ? html`<${Loader} size=${28} />` : html`<button class="btn text sm" onClick=${() => loadMessages(partner, true)}>Load older messages</button>`}</div>` : null}
      ${thread?.error ? html`<div class="body-m" style="color:var(--error);text-align:center;padding:12px">${thread.error}</div>` : null}
      ${thread?.loaded && !thread.items.length ? html`<div class="body-m muted" style="text-align:center;padding:40px 12px">No messages yet — say hi 👋</div>` : null}
      ${groups.map((g) => g.sep ? html`<div class="day-sep" key=${g.key}>${g.sep}</div>` : html`<${Message} key=${g.m.id} g=${g} p=${p} partner=${partner} />`)}
      ${isTyping ? html`<div class="typing" aria-label="typing"><i></i><i></i><i></i></div>` : null}
    </div>
    ${files.length ? html`<div class="pending-img">${files.map((f, i) => html`<div class="pi" key=${f.url}><img src=${f.url} alt="" />
      <button class="icon-btn" aria-label="Remove image" onClick=${() => setFiles(files.filter((_, k) => k !== i))}><${Icon} name="close" /></button></div>`)}</div>` : null}
    <${Composer} partner=${partner} files=${files} setFiles=${setFiles} addFiles=${addFiles} onSent=${() => (stick.current = true)} />
    ${drag ? html`<div class="drop-hint"><div class="col" style="align-items:center"><${Icon} name="add_photo_alternate" size="xl" />Drop images to send</div></div>` : null}
  </section>`;
}

function Message({ g, p, partner }) {
  const { m, first, last } = g;
  const media = isMediaOnly(m.text);
  const retry = async () => {
    removeMessage(partner, m.id);
    sendText(partner, m.text);
  };
  return html`<div class=${`msg-row ${m.outgoing ? "out" : "in"} ${first ? "first" : ""} ${last ? "last" : ""}`} title=${new Date(m.timestamp * 1000).toLocaleString()}>
    ${!m.outgoing ? html`<div class="avatar-slot">${last ? html`<${Avatar} src=${p.avatar} size=${28} round name=${p.name} />` : null}</div>` : null}
    <div class="col" style="gap:2px;align-items:${m.outgoing ? "flex-end" : "flex-start"};min-width:0">
      <div class=${`bubble ${media ? "media" : ""}`} style=${m.pending ? "opacity:.6" : ""}><${BBCode} text=${m.text} />${last && !media ? html`<span class="msg-time">${clock(m.timestamp)}</span>` : null}</div>
      ${m.failed ? html`<div class="row body-s" style="color:var(--error)">⚠ ${m.failed}<button class="btn text sm" onClick=${retry}>Retry</button></div>` : null}
    </div>
  </div>`;
}

async function sendText(partner, text) {
  const id = addPendingMessage(partner, text);
  try {
    const m = await post(`/chat/${partner}/send`, { text });
    removeMessage(partner, id);
    // the event stream normally delivers it; make sure it's there
    const thread = messages.value.get(partner);
    if (thread && !thread.items.some((x) => x.id === m.id)) {
      messages.value = new Map(messages.value).set(partner, { ...thread, items: [...thread.items, m] });
    }
  } catch (e) {
    failPendingMessage(partner, id, e.message);
  }
}

function Composer({ partner, files, setFiles, addFiles, onSent }) {
  const [text, setText] = useState(() => sessionStorage.getItem(`draft:${partner}`) || "");
  const [sending, setSending] = useState(false);
  const ta = useRef();
  const fileInput = useRef();
  const lastTyping = useRef(0);
  useEffect(() => ta.current && ta.current.focus(), [partner]);
  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }, [text]);
  const onInput = (e) => {
    setText(e.target.value);
    sessionStorage.setItem(`draft:${partner}`, e.target.value);
    if (Date.now() - lastTyping.current > 4000 && e.target.value) {
      lastTyping.current = Date.now();
      post(`/chat/${partner}/typing`).catch(() => {});
    }
  };
  const send = async () => {
    const t = text.trim();
    if (!t && !files.length) return;
    onSent();
    setText("");
    sessionStorage.removeItem(`draft:${partner}`);
    if (files.length) {
      setSending(true);
      const queue = files;
      setFiles([]);
      for (const f of queue) {
        const fd = new FormData();
        fd.append("file", f.file);
        try {
          await api(`/chat/${partner}/image`, { method: "POST", form: fd });
        } catch (e) {
          toastError(e);
        }
        URL.revokeObjectURL(f.url);
      }
      setSending(false);
    }
    if (t) await sendText(partner, t);
  };
  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  };
  const onPaste = (e) => {
    const imgs = [...(e.clipboardData?.files || [])];
    if (imgs.length) {
      e.preventDefault();
      addFiles(imgs);
    }
  };
  const ready = !!text.trim() || files.length > 0;
  return html`<div class="composer glass">
    <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden ref=${fileInput} onChange=${(e) => { addFiles(e.target.files); e.target.value = ""; }} />
    <button class="icon-btn" aria-label="Attach image" title="Attach image" onClick=${() => fileInput.current.click()}><${Icon} name="add_photo_alternate" /></button>
    <textarea ref=${ta} rows="1" placeholder="Message" value=${text} onInput=${onInput} onKeyDown=${onKey} onPaste=${onPaste} aria-label="Message"></textarea>
    <button class=${`icon-btn send ${ready ? "ready" : ""}`} aria-label="Send" disabled=${!ready || sending} onClick=${send}>
      ${sending ? html`<${Loader} size=${22} onColor />` : html`<${Icon} name="send" />`}</button>
  </div>`;
}
