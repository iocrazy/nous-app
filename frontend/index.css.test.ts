/**
 * Regression guard for the tinted-button system (`.btn-tint-*`) in index.css.
 *
 * Bug: the base `.btn-tint-*` rules hard-code a light, dark-theme text color
 * (e.g. indigo `#a5b4fc`). In light theme the background stays a pale tint on
 * white, so pale text on a pale ground drops the New Issue / filter buttons to
 * an unreadable contrast. The fix adds `[data-theme="light"]` overrides with
 * saturated dark text.
 *
 * This test parses the CSS directly (no browser) and asserts every tinted
 * button clears WCAG AA (4.5:1) in BOTH themes, compositing the tint over the
 * theme's island background.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

// vitest runs with cwd at the frontend package root, where index.css lives.
const CSS = readFileSync(path.resolve(process.cwd(), 'index.css'), 'utf8');

const TINTS = ['indigo', 'amber', 'violet', 'green', 'red'] as const;

// Island background each button sits on: dark islands ≈ #15151a, light ≈ #ffffff.
const ISLAND_DARK: RGB = [0x15, 0x15, 0x1a];
const ISLAND_LIGHT: RGB = [0xff, 0xff, 0xff];

type RGB = [number, number, number];
type RGBA = [number, number, number, number];

function parseColor(raw: string): RGBA {
  const s = raw.trim();
  const hex = s.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  const rgba = s.match(/rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)/i);
  if (rgba) {
    return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), rgba[4] === undefined ? 1 : Number(rgba[4])];
  }
  throw new Error(`Unparseable color: ${raw}`);
}

/** Composite a possibly-translucent foreground over an opaque background. */
function composite([r, g, b, a]: RGBA, bg: RGB): RGB {
  return [r, g, b].map((c, i) => Math.round(a * c + (1 - a) * bg[i])) as RGB;
}

function relLuminance([r, g, b]: RGB): number {
  const f = (c: number) => {
    const cs = c / 255;
    return cs <= 0.03928 ? cs / 12.92 : ((cs + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(fg: RGB, bg: RGB): number {
  const l1 = relLuminance(fg);
  const l2 = relLuminance(bg);
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

/** Extract the `{ … }` declaration body for an exact selector from the CSS. */
function ruleBody(selector: string): string | null {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = CSS.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  return m ? m[1] : null;
}

function decl(body: string, prop: string): string | null {
  // `background` matches, but not `background-clip`; `border-color` is distinct.
  const m = body.match(new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`, 'i'));
  return m ? m[1].trim() : null;
}

/**
 * Effective text color + tint background for a tint in a given theme, applying
 * the cascade: the light override wins when present, else the base rule.
 */
function effective(tint: string, theme: 'dark' | 'light') {
  const base = ruleBody(`.btn-tint-${tint}`);
  if (!base) throw new Error(`missing base rule for .btn-tint-${tint}`);
  const override = theme === 'light' ? ruleBody(`[data-theme="light"] .btn-tint-${tint}`) : null;

  const pick = (prop: string) =>
    (override && decl(override, prop)) || decl(base, prop);

  return {
    color: pick('color'),
    background: pick('background'),
  };
}

describe('btn-tint contrast', () => {
  for (const tint of TINTS) {
    it(`dark theme: .btn-tint-${tint} text clears WCAG AA`, () => {
      const { color, background } = effective(tint, 'dark');
      expect(color, `no color for ${tint}`).toBeTruthy();
      expect(background, `no background for ${tint}`).toBeTruthy();
      const bg = composite(parseColor(background!), ISLAND_DARK);
      const fg = composite(parseColor(color!), bg);
      expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5);
    });

    it(`light theme: .btn-tint-${tint} text clears WCAG AA`, () => {
      const { color, background } = effective(tint, 'light');
      const bg = composite(parseColor(background!), ISLAND_LIGHT);
      const fg = composite(parseColor(color!), bg);
      expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5);
    });
  }
});
