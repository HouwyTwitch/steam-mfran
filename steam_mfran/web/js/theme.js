// Material You dynamic colour: generate all colour roles from one seed.
import { argbFromHex, hexFromArgb, argbFromRgb, Hct, SchemeTonalSpot, SchemeVibrant, SchemeExpressive, MaterialDynamicColors as M, QuantizerCelebi, Score } from "../vendor/mcu.js";

const ROLES = [
  "primary", "onPrimary", "primaryContainer", "onPrimaryContainer", "secondary", "onSecondary", "secondaryContainer", "onSecondaryContainer",
  "tertiary", "onTertiary", "tertiaryContainer", "onTertiaryContainer", "error", "onError", "errorContainer", "onErrorContainer",
  "surface", "onSurface", "onSurfaceVariant", "surfaceDim", "surfaceBright", "surfaceContainerLowest", "surfaceContainerLow",
  "surfaceContainer", "surfaceContainerHigh", "surfaceContainerHighest", "outline", "outlineVariant", "inverseSurface",
  "inverseOnSurface", "inversePrimary", "scrim", "shadow", "primaryFixed", "primaryFixedDim", "tertiaryFixed", "tertiaryFixedDim",
];
const kebab = (s) => s.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
export const DEFAULT_SEED = "#1a9fff";
export const SWATCHES = ["#1a9fff", "#6750a4", "#00897b", "#e8710a", "#d81b60", "#7cb342", "#5c6bc0", "#8d6e63"];

const media = window.matchMedia("(prefers-color-scheme: dark)");
let current = { theme: "system", seed: DEFAULT_SEED, style: "vibrant" };
const avatarSeeds = new Map();

export function isDark(theme = current.theme) {
  return theme === "dark" || (theme === "system" && media.matches);
}

function scheme(seed, dark, style) {
  const hct = Hct.fromInt(argbFromHex(seed));
  const Cls = style === "expressive" ? SchemeExpressive : style === "tonal" ? SchemeTonalSpot : SchemeVibrant;
  return new Cls(hct, dark, 0);
}

export function applyTheme({ theme, seed, style } = {}) {
  current = { theme: theme ?? current.theme, seed: seed || DEFAULT_SEED, style: style ?? current.style };
  const dark = isDark(current.theme);
  const s = scheme(current.seed, dark, current.style);
  const root = document.documentElement;
  for (const r of ROLES) root.style.setProperty(`--${kebab(r)}`, hexFromArgb(M[r].getArgb(s)));
  root.dataset.scheme = dark ? "dark" : "light";
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = hexFromArgb(M.surface.getArgb(s));
}
media.addEventListener("change", () => applyTheme(current));

/** Extract a seed colour from an avatar (needs CORS-enabled image). */
export function seedFromImage(url) {
  if (!url) return Promise.resolve(null);
  if (avatarSeeds.has(url)) return Promise.resolve(avatarSeeds.get(url));
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        const size = 64;
        c.width = c.height = size;
        const ctx = c.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, size, size);
        const data = ctx.getImageData(0, 0, size, size).data;
        const pixels = [];
        for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 200) pixels.push(argbFromRgb(data[i], data[i + 1], data[i + 2]));
        const ranked = Score.score(QuantizerCelebi.quantize(pixels, 128));
        const hex = ranked.length ? hexFromArgb(ranked[0]) : null;
        avatarSeeds.set(url, hex);
        resolve(hex);
      } catch {
        resolve(null); // tainted canvas: avatar CDN without CORS
      }
    };
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

// M3 Expressive loading indicator: morph between rounded polygons via clip-path.
(function injectMorph() {
  const N = 60;
  const shapes = [
    (t) => 1 - 0.1 * (0.5 + 0.5 * Math.cos(9 * t)), // 9-sided cookie
    (t) => 0.72 + 0.28 * Math.pow(Math.abs(Math.cos(2 * t)), 0.6), // clover
    (t) => 1 - 0.16 * (0.5 + 0.5 * Math.cos(5 * t)), // soft pentagon
    (t) => 0.86 + 0.14 * Math.cos(12 * t), // burst
    (t) => 1 - 0.06 * (0.5 + 0.5 * Math.cos(4 * t)), // squircle-ish
  ];
  const poly = (f) =>
    "polygon(" +
    Array.from({ length: N }, (_, i) => {
      const t = (i / N) * Math.PI * 2;
      const r = f(t) * 50;
      return `${(50 + r * Math.cos(t)).toFixed(2)}% ${(50 + r * Math.sin(t)).toFixed(2)}%`;
    }).join(",") +
    ")";
  const frames = shapes.map((f, i) => `${Math.round((i / shapes.length) * 100)}% { clip-path: ${poly(f)}; }`).join("\n");
  const style = document.createElement("style");
  style.textContent = `@keyframes morph {\n${frames}\n100% { clip-path: ${poly(shapes[0])}; }\n}`;
  document.head.appendChild(style);
})();
