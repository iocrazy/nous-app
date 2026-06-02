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
}

// Neutral, non-blue fallbacks.
const FALLBACK: SodaTheme = {
  accent: '#e4e4e7', // zinc-200
  accentSoft: 'rgba(228,228,231,0.6)',
  waveUnplayed: 'rgba(113,113,122,0.5)', // zinc-500
  lyricActive: '#fafafa',
  lyricNormal: 'rgba(161,161,170,0.7)', // zinc-400
  gradientCss: 'linear-gradient(180deg,#18181b,#000)',
  bg: '#09090b', // zinc-950
  onAccent: '#0a0a0a',
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

/** Build an rgba string from an [r,g,b] triple at a given alpha. */
function rgba([r, g, b]: [number, number, number], a: number): string {
  return `rgba(${r},${g},${b},${a})`;
}

/**
 * Build a full SodaTheme from a (possibly malformed / absent) palette.
 * Any unusable field independently falls back to its neutral default.
 */
export function buildSodaTheme(colors?: SodaColors | null): SodaTheme {
  if (!colors || typeof colors !== 'object') return { ...FALLBACK };

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

  return { accent, accentSoft, waveUnplayed, lyricActive, lyricNormal, gradientCss, bg, onAccent };
}
