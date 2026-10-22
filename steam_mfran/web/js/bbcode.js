// Steam chat BBCode → Preact vnodes.
// Tags seen in Steam friend chat: url, img, emoticon, sticker, spoiler, quote, code, pre,
// og, tradeoffer, tradeofferlink, gameinvite, lobbyinvite, random, flip, mention, b, i, u, s.
import { html, useState } from "../vendor/preact.js";
import { lightbox } from "./store.js";

const ESC = "\u0000LB\u0000";
const TAG_RE = /\[(\/?)([a-z]+)((?:=[^\]]*)|(?:\s[^\]]*))?\]/gi;
const ATTR_RE = /([a-z_]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"']+))/gi;
const KNOWN = new Set(["url", "img", "emoticon", "sticker", "spoiler", "quote", "code", "pre", "og", "tradeoffer", "tradeofferlink",
  "gameinvite", "lobbyinvite", "random", "flip", "mention", "b", "i", "u", "s", "roomeffect"]);
const EMOTICON_URL = "https://community.fastly.steamstatic.com/economy/emoticon/";
const STICKER_URL = "https://community.fastly.steamstatic.com/economy/sticker/";

function parseAttrs(raw) {
  const attrs = {};
  if (!raw) return attrs;
  if (raw.startsWith("=")) {
    // [url=https://...] or [mention=123]
    const m = raw.slice(1).match(/^"([^"]*)"|^([^\s]*)/);
    attrs._ = (m && (m[1] ?? m[2])) || "";
    raw = raw.slice(1 + (m ? m[0].length : 0));
  }
  let m;
  ATTR_RE.lastIndex = 0;
  while ((m = ATTR_RE.exec(raw))) attrs[m[1].toLowerCase()] = m[2] ?? m[3] ?? m[4] ?? "";
  return attrs;
}

/** Parse into a tree: {tag, attrs, children} | string */
export function parse(text) {
  const src = (text || "").replace(/\\\[/g, ESC);
  const root = { tag: "root", attrs: {}, children: [] };
  const stack = [root];
  let last = 0;
  TAG_RE.lastIndex = 0;
  let m;
  while ((m = TAG_RE.exec(src))) {
    const [full, closing, name] = m;
    const tag = name.toLowerCase();
    if (!KNOWN.has(tag)) continue;
    const top = stack[stack.length - 1];
    if (m.index > last) top.children.push(src.slice(last, m.index));
    last = m.index + full.length;
    if (closing) {
      const idx = stack.map((n) => n.tag).lastIndexOf(tag);
      if (idx > 0) stack.length = idx;
      else top.children.push(full);
    } else {
      const node = { tag, attrs: parseAttrs(m[3]), children: [] };
      top.children.push(node);
      stack.push(node);
    }
  }
  if (last < src.length) stack[stack.length - 1].children.push(src.slice(last));
  return root;
}

const unesc = (s) => s.split(ESC).join("[");
const textOf = (node) => (typeof node === "string" ? unesc(node) : node.children.map(textOf).join(""));
const URL_RE = /(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g;

function linkify(str, key) {
  const parts = unesc(str).split(URL_RE);
  return parts.map((p, i) => (i % 2 ? html`<a key=${`${key}-${i}`} href=${p} target="_blank" rel="noreferrer noopener">${p}</a>` : p));
}

function Spoiler({ children }) {
  const [open, setOpen] = useState(false);
  return html`<span class=${`spoiler ${open ? "open" : ""}`} onClick=${() => setOpen(!open)} title="Spoiler">${children}</span>`;
}

function render(node, key = "n") {
  if (typeof node === "string") return linkify(node, key);
  const kids = () => node.children.map((c, i) => render(c, `${key}-${i}`));
  const a = node.attrs;
  switch (node.tag) {
    case "root":
      return kids();
    case "b":
      return html`<strong key=${key}>${kids()}</strong>`;
    case "i":
      return html`<em key=${key}>${kids()}</em>`;
    case "u":
      return html`<u key=${key}>${kids()}</u>`;
    case "s":
      return html`<s key=${key}>${kids()}</s>`;
    case "url": {
      const href = a._ || a.url || textOf(node);
      if (!/^https?:\/\//i.test(href)) return kids();
      return html`<a key=${key} href=${href} target="_blank" rel="noreferrer noopener">${node.children.length ? kids() : href}</a>`;
    }
    case "img": {
      const src = a.src || a._ || textOf(node).trim();
      if (!/^(https?:|data:image\/)/i.test(src)) return null;
      return html`<img key=${key} class="chat-img" src=${src} alt="Image" loading="lazy" onClick=${() => (lightbox.value = src)} />`;
    }
    case "emoticon": {
      const name = textOf(node).trim().replace(/^:|:$/g, "");
      return html`<img key=${key} class="emoticon" src=${EMOTICON_URL + encodeURIComponent(name)} alt=${`:${name}:`} title=${`:${name}:`} />`;
    }
    case "sticker": {
      const type = a.type || a._ || "";
      return html`<img key=${key} class="sticker" src=${STICKER_URL + encodeURIComponent(type)} alt=${`Sticker ${type}`} />`;
    }
    case "spoiler":
      return html`<${Spoiler} key=${key}>${kids()}<//>`;
    case "quote":
      return html`<blockquote key=${key}>${a.author ? html`<div class="label-m">${a.author}</div>` : null}${kids()}</blockquote>`;
    case "code":
      return html`<code key=${key}>${textOf(node)}</code>`;
    case "pre":
      return html`<pre key=${key}>${textOf(node)}</pre>`;
    case "og": {
      const href = a.url || a._;
      return html`<a key=${key} class="og" href=${href} target="_blank" rel="noreferrer noopener">
        ${a.title ? html`<strong>${a.title}</strong><br />` : null}<span class="body-s">${textOf(node) || href}</span></a>`;
    }
    case "tradeoffer":
    case "tradeofferlink":
      return html`<span key=${key} class="tradeoffer">🔁 Trade offer ${a.id ? `#${a.id}` : ""}</span>`;
    case "gameinvite":
    case "lobbyinvite":
      return html`<span key=${key} class="tradeoffer">🎮 Game invite</span>`;
    case "random":
      return html`<span key=${key}>🎲 ${a.result ?? textOf(node)}</span>`;
    case "flip":
      return html`<span key=${key}>🪙 ${a.result ?? textOf(node)}</span>`;
    case "mention":
      return html`<strong key=${key}>${kids()}</strong>`;
    case "roomeffect":
      return html`<span key=${key}>✨ ${a.type || "effect"}</span>`;
    default:
      return kids();
  }
}

export function BBCode({ text }) {
  return render(parse(text));
}

/** True when the message is only an image (render without a bubble background). */
export function isMediaOnly(text) {
  const root = parse(text);
  const parts = root.children.filter((c) => !(typeof c === "string" && !c.trim()));
  return parts.length === 1 && typeof parts[0] !== "string" && ["img", "sticker"].includes(parts[0].tag);
}
