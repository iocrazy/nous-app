/**
 * Soda (qishui) color theming.
 *
 * Soda audio items carry their own color palette in `metadata.colors`. These
 * pure, defensive helpers translate that palette into concrete CSS colors the
 * audio player + detail page can consume. Every fallback is a NEUTRAL non-blue
 * shade (zinc / white / near-black) — never blue, indigo, or purple.
 */

/** One raw palette entry as stored on `metadata.colors`. */
export interface SodaColorEntry {
  rgb?: string;
  alpha?: string;
}

/** Subset of `metadata.colors` consumed by the theme builder. */
export interface SodaColors {
  playing_wave_color?: SodaColorEntry;
  paused_wave_color?: SodaColorEntry;
  playing_lyric_color?: SodaColorEntry;
  normal_lyric_color?: SodaColorEntry;
  background_color?: SodaColorEntry;
  cover_gradient_effect_color?: Array<SodaColorEntry>;
  base_colors?: Array<SodaColorEntry>;
}

export interface SodaTheme {
  /** playing_wave_color → button / cursor / volume thumb / played waveform. */
  accent: string;
  /** accent dimmed (~0.6 alpha) — used for the played-wave lower mirror. */
  accentSoft: string;
  /** paused_wave_color → unplayed waveform. */
  waveUnplayed: string;
  /** playing_lyric_color → active lyric line. */
  lyricActive: string;
  /** normal_lyric_color → inactive lyric lines. */
  lyricNormal: string;
  /** Vertical gradient backdrop from cover_gradient_effect_color. */
  gradientCss: string;
  /** background_color → deep page/panel background. */
  bg: string;
  /** '#0a0a0a' or '#ffffff' — readable icon color ON the accent button. */
  onAccent: string;
  /** Readable text ramp ON `bg` (primary → quaternary). Black-or-white chosen by
   *  WCAG contrast against `bg`, so the title/artist/stats stay legible on ANY
   *  stored background color (the deep-green-vs-pale-green readability bug). */
  onBg: string;
  onBg2: string;
  onBg3: string;
  onBg4: string;
}

/** Dark-text ramp — for light backgrounds. */
const DARK_TEXT = {
  onBg: 'rgba(0,0,0,0.92)',
  onBg2: 'rgba(0,0,0,0.66)',
  onBg3: 'rgba(0,0,0,0.5)',
  onBg4: 'rgba(0,0,0,0.34)',
};
/** Light-text ramp — for dark backgrounds. */
const LIGHT_TEXT = {
  onBg: 'rgba(255,255,255,0.95)',
  onBg2: 'rgba(255,255,255,0.72)',
  onBg3: 'rgba(255,255,255,0.55)',
  onBg4: 'rgba(255,255,255,0.4)',
};

// Neutral, non-blue fallbacks. (bg is near-black → light text ramp.)
const FALLBACK: SodaTheme = {
  accent: '#e4e4e7', // zinc-200
  accentSoft: 'rgba(228,228,231,0.6)',
  waveUnplayed: 'rgba(113,113,122,0.5)', // zinc-500
  lyricActive: '#fafafa',
  lyricNormal: 'rgba(161,161,170,0.7)', // zinc-400
  gradientCss: 'linear-gradient(180deg,#18181b,#000)',
  bg: '#09090b', // zinc-950
  onAccent: '#0a0a0a',
  ...LIGHT_TEXT,
};

const HEX6 = /^[0-9a-fA-F]{6}$/;

/** Parse a 6-char hex into [r,g,b] (0-255), or null if malformed. */
function parseRgb(rgb?: string): [number, number, number] | null {
  if (typeof rgb !== 'string') return null;
  const clean = rgb.trim().replace(/^#/, '');
  if (!HEX6.test(clean)) return null;
  const n = parseInt(clean, 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

/** Parse an alpha hex byte ("bf") → 0..1, or null if malformed/absent. */
function parseAlpha(alpha?: string): number | null {
  if (typeof alpha !== 'string') return null;
  const clean = alpha.trim().replace(/^#/, '');
  if (!/^[0-9a-fA-F]{1,2}$/.test(clean)) return null;
  const byte = parseInt(clean.padStart(2, '0'), 16);
  if (Number.isNaN(byte)) return null;
  return Math.max(0, Math.min(1, byte / 255));
}

/**
 * Convert a palette entry to a CSS color string.
 * → '#rrggbb' when no alpha, 'rgba(r,g,b,a)' when alpha present.
 * → null for malformed / missing input (caller substitutes a fallback).
 */
export function colorFrom(entry?: SodaColorEntry | null): string | null {
  if (!entry) return null;
  const rgb = parseRgb(entry.rgb);
  if (!rgb) return null;
  const [r, g, b] = rgb;
  const a = parseAlpha(entry.alpha);
  if (a === null) {
    return `#${rgb.map((c) => c.toString(16).padStart(2, '0')).join('')}`;
  }
  return `rgba(${r},${g},${b},${Number(a.toFixed(3))})`;
}

/** Relative luminance (0..1, sRGB approximation) of an [r,g,b] triple. */
function luminance([r, g, b]: [number, number, number]): number {
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}

/** One sRGB channel (0..255) → linear-light (0..1), per WCAG. */
function srgbToLinear(c: number): number {
  const cs = c / 255;
  return cs <= 0.03928 ? cs / 12.92 : Math.pow((cs + 0.055) / 1.055, 2.4);
}

/** WCAG relative luminance (0..1) of an [r,g,b] triple (gamma-corrected). */
function relLuminance([r, g, b]: [number, number, number]): number {
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}

/**
 * Pick the more legible text ramp (black-vs-white) for a given background by
 * comparing actual WCAG contrast ratios — correct across the whole gamut, unlike
 * a single linear-luminance threshold. Returns the {onBg..onBg4} ramp.
 */
function readableRamp(bgRgb: [number, number, number]): Pick<SodaTheme, 'onBg' | 'onBg2' | 'onBg3' | 'onBg4'> {
  const l = relLuminance(bgRgb);
  const contrastWhite = 1.05 / (l + 0.05);
  const contrastBlack = (l + 0.05) / 0.05;
  return contrastBlack >= contrastWhite ? DARK_TEXT : LIGHT_TEXT;
}

/** Build an rgba string from an [r,g,b] triple at a given alpha. */
function rgba([r, g, b]: [number, number, number], a: number): string {
  return `rgba(${r},${g},${b},${a})`;
}

/** Stable 32-bit hash of a string (djb2). Same seed → same hue. */
function hashSeed(seed: string): number {
  let h = 5381;
  for (let i = 0; i < seed.length; i++) h = ((h << 5) + h + seed.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** HSL (h 0-360, s/l 0-1) → [r,g,b] 0-255. */
function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = h / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  const [r1, g1, b1] =
    hp < 1 ? [c, x, 0]
    : hp < 2 ? [x, c, 0]
    : hp < 3 ? [0, c, x]
    : hp < 4 ? [0, x, c]
    : hp < 5 ? [x, 0, c]
    : [c, 0, x];
  const m = l - c / 2;
  return [
    Math.round((r1 + m) * 255),
    Math.round((g1 + m) * 255),
    Math.round((b1 + m) * 255),
  ];
}

/**
 * A pleasant, deterministic-per-seed theme for audio that has NO Soda palette
 * (extracted audio, uploads, non-qishui). Each track/file gets a stable vivid
 * hue so the player is colorful + consistent rather than flat gray.
 */
function randomThemeFromSeed(seed: string): SodaTheme {
  const hue = hashSeed(seed) % 360;
  const accentRgb = hslToRgb(hue, 0.62, 0.58); // vivid but not neon
  const accentHex = `#${accentRgb.map((c) => c.toString(16).padStart(2, '0')).join('')}`;
  const bgRgb = hslToRgb(hue, 0.4, 0.07);
  return {
    accent: accentHex,
    accentSoft: rgba(accentRgb, 0.6),
    waveUnplayed: rgba(hslToRgb(hue, 0.18, 0.45), 0.5), // desaturated
    lyricActive: `#${hslToRgb(hue, 0.7, 0.85).map((c) => c.toString(16).padStart(2, '0')).join('')}`,
    lyricNormal: rgba(hslToRgb(hue, 0.22, 0.62), 0.7),
    gradientCss: `linear-gradient(180deg, ${rgba(hslToRgb(hue, 0.4, 0.12), 1)} 0%, #000 100%)`,
    bg: rgba(bgRgb, 1),
    onAccent: luminance(accentRgb) > 0.6 ? '#0a0a0a' : '#ffffff',
    ...readableRamp(bgRgb),
  };
}

/**
 * Build a full SodaTheme from a (possibly malformed / absent) palette.
 * Any unusable field independently falls back to its neutral default.
 *
 * When no usable palette is given but a ``seed`` is provided, returns a
 * deterministic vivid theme derived from the seed (so non-Soda audio still
 * gets a per-track color instead of flat gray).
 */
export function buildSodaTheme(colors?: SodaColors | null, seed?: string): SodaTheme {
  if (!colors || typeof colors !== 'object') {
    return seed ? randomThemeFromSeed(seed) : { ...FALLBACK };
  }

  const accentRgb = parseRgb(colors.playing_wave_color?.rgb);
  const accent = accentRgb
    ? colorFrom({ rgb: colors.playing_wave_color?.rgb }) ?? FALLBACK.accent
    : FALLBACK.accent;
  const accentSoft = accentRgb ? rgba(accentRgb, 0.6) : FALLBACK.accentSoft;
  const onAccent = accentRgb
    ? luminance(accentRgb) > 0.6
      ? '#0a0a0a'
      : '#ffffff'
    : FALLBACK.onAccent;

  const waveUnplayed = colorFrom(colors.paused_wave_color) ?? FALLBACK.waveUnplayed;
  const lyricActive = colorFrom(colors.playing_lyric_color) ?? FALLBACK.lyricActive;
  const lyricNormal = colorFrom(colors.normal_lyric_color) ?? FALLBACK.lyricNormal;
  const bg = colorFrom(colors.background_color) ?? FALLBACK.bg;

  // Readable text ramp ON bg: pick from the real bg rgb when present, else keep
  // the dark-bg (light text) fallback ramp.
  const bgRgb = parseRgb(colors.background_color?.rgb);
  const ramp = bgRgb
    ? readableRamp(bgRgb)
    : { onBg: FALLBACK.onBg, onBg2: FALLBACK.onBg2, onBg3: FALLBACK.onBg3, onBg4: FALLBACK.onBg4 };

  // Gradient from the 2-entry cover_gradient_effect_color array.
  let gradientCss = FALLBACK.gradientCss;
  const grad = colors.cover_gradient_effect_color;
  if (Array.isArray(grad) && grad.length >= 2) {
    const top = colorFrom(grad[0]);
    const bottom = colorFrom(grad[1]);
    if (top && bottom) {
      gradientCss = `linear-gradient(180deg, ${top} 0%, ${bottom} 100%)`;
    }
  }

  return { accent, accentSoft, waveUnplayed, lyricActive, lyricNormal, gradientCss, bg, onAccent, ...ramp };
}
