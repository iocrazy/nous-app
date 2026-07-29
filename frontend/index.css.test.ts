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

/**
 * K1 (2026-07-29) recolor regression guard — 全站配色重构 W1
 * (docs/superpowers/specs/2026-07-29-warm-paper-palette-design.md §2.1).
 *
 * `@theme` now overrides Tailwind v4's default indigo/violet/purple/emerald/
 * green/amber/red/rose/blue/sky 50-950 ladders with 5 new low-saturation
 * anchors. These assertions pin the exact anchor hex (@600) so any future
 * edit that accidentally reverts a hue back to its stock Tailwind value (or
 * silently drifts off the approved mockup) fails CI instead of shipping.
 */
describe('K1 hue-ladder remap', () => {
  const HUE_TO_ANCHOR: Record<string, string> = {
    indigo: '#1E7A5B',
    violet: '#1E7A5B',
    emerald: '#1E7A5B',
    green: '#1E7A5B',
    purple: '#7A5E8F',
    amber: '#A87B2B',
    red: '#AD5147',
    rose: '#AD5147',
    blue: '#46708E',
    sky: '#46708E',
  };

  for (const [hue, anchor] of Object.entries(HUE_TO_ANCHOR)) {
    it(`--color-${hue}-600 is the new anchor (${anchor}), not the stock Tailwind hue`, () => {
      const m = CSS.match(new RegExp(`--color-${hue}-600:\\s*([^;]+);`));
      expect(m, `--color-${hue}-600 not found in @theme`).toBeTruthy();
      expect(m![1].trim().toUpperCase()).toBe(anchor.toUpperCase());
    });
  }

  it('old indigo-500 stock hex (#6366f1) is gone from --color-accent', () => {
    const m = CSS.match(/--color-accent:\s*([^;]+);/);
    expect(m).toBeTruthy();
    expect(m![1].trim().toLowerCase()).not.toBe('#6366f1');
    expect(m![1].trim().toUpperCase()).toBe('#1E7A5B');
  });

  it('semantic tokens (ok/warn/danger/info/agent) are declared in the light theme block', () => {
    const lightBlock = CSS.split('[data-theme="light"] {')[1];
    expect(lightBlock, 'no [data-theme="light"] block found').toBeTruthy();
    for (const token of ['--ok', '--warn', '--danger', '--info', '--agent']) {
      expect(lightBlock!.includes(`${token}:`), `${token} missing from light block`).toBe(true);
      expect(lightBlock!.includes(`${token}-soft:`), `${token}-soft missing from light block`).toBe(true);
      expect(lightBlock!.includes(`${token}-line:`), `${token}-line missing from light block`).toBe(true);
    }
  });

  it('semantic tokens are aliased into Tailwind utilities via @theme inline', () => {
    for (const token of ['ok', 'warn', 'danger', 'info', 'agent']) {
      expect(CSS.includes(`--color-${token}: var(--${token});`), `--color-${token} not aliased`).toBe(true);
    }
  });
});

/**
 * K1.5 (2026-07-29) per-module accent scoping regression guard — 全站配色
 * 重构 W1.5 (docs/superpowers/specs/2026-07-29-warm-paper-palette-design.md
 * §5). `[data-module="x"]` blocks re-point ONLY the 4 accent ladders
 * (indigo/violet/emerald/green); the 6 semantic ladders (purple/amber/red/
 * rose/blue/sky) plus the ok/warn/danger/info/agent tokens must stay
 * untouched by these blocks (module scoping ≠ semantic recolor).
 */
describe('K1.5 per-module accent scoping', () => {
  const MODULE_ANCHOR: Record<string, string> = {
    ai: '#7A5E8F',           // 李紫 — identical to the global purple ladder
    inspiration: '#956C25',  // 赭, darkened for module-accent contrast (see index.css comment)
    resources: '#46708E',    // 钢蓝 — identical to the global blue/sky ladder
  };
  const ACCENT_HUES = ['indigo', 'violet', 'emerald', 'green'];
  const SEMANTIC_HUES = ['purple', 'amber', 'red', 'rose', 'blue', 'sky'];

  function moduleBlock(module: string): string {
    const m = CSS.match(new RegExp(`\\[data-module=(?:"${module}"|${module})\\]\\s*\\{([^}]*)\\}`));
    expect(m, `[data-module="${module}"] block not found`).toBeTruthy();
    return m![1];
  }

  for (const [module, anchor] of Object.entries(MODULE_ANCHOR)) {
    for (const hue of ACCENT_HUES) {
      it(`[data-module="${module}"] overrides --color-${hue}-600 to ${anchor}`, () => {
        const block = moduleBlock(module);
        const m = block.match(new RegExp(`--color-${hue}-600:\\s*([^;]+);`));
        expect(m, `--color-${hue}-600 not overridden in [data-module="${module}"]`).toBeTruthy();
        expect(m![1].trim().toUpperCase()).toBe(anchor.toUpperCase());
      });
    }

    it(`[data-module="${module}"] does NOT touch the semantic hues (purple/amber/red/rose/blue/sky)`, () => {
      const block = moduleBlock(module);
      for (const hue of SEMANTIC_HUES) {
        expect(
          new RegExp(`--color-${hue}-\\d+:`).test(block),
          `[data-module="${module}"] unexpectedly touches --color-${hue}-*`,
        ).toBe(false);
      }
    });
  }

  it('inspiration-module ochre-600 (#956C25) clears 4.5:1 vs white and paper (#FCFBF8)', () => {
    // Mirrors the contrast() helper above but re-declared locally to avoid
    // coupling this describe block to the btn-tint fixtures' RGB tuples.
    const hex = MODULE_ANCHOR.inspiration;
    const bgWhite: RGB = [0xff, 0xff, 0xff];
    const bgPaper: RGB = [0xfc, 0xfb, 0xf8];
    const fg = parseColor(hex).slice(0, 3) as unknown as RGB;
    expect(contrast(fg, bgWhite)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(fg, bgPaper)).toBeGreaterThanOrEqual(4.5);
  });

  it('inspiration-module ochre soft/line (50-300) stay identical to the global ochre ladder', () => {
    const block = moduleBlock('inspiration');
    const GLOBAL_SOFT_LINE: Record<number, string> = {
      50: '#FBF4EB', 100: '#F3EAD6', 200: '#EFDFC6', 300: '#E2D2AC',
    };
    for (const [step, hex] of Object.entries(GLOBAL_SOFT_LINE)) {
      const m = block.match(new RegExp(`--color-indigo-${step}:\\s*([^;]+);`));
      expect(m, `--color-indigo-${step} missing`).toBeTruthy();
      expect(m![1].trim().toUpperCase()).toBe(hex.toUpperCase());
    }
  });
});
