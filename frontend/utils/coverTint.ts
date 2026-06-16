export interface CoverTint { tint: string; tintDeep: string }

const INDIGO: CoverTint = { tint: '99,102,241', tintDeep: '24,28,46' };

// How far to pull a sampled/accent color toward its own gray (0 = keep as-is,
// 1 = fully desaturated). The mock's hand-picked tint is low-saturation; raw
// cover samples (e.g. a saturated red album cover) would otherwise wash the
// whole stage in vivid color. Muting keeps just a hint of the cover's hue so the
// gradient stays soft like the mock regardless of how saturated the art is.
const MUTE = 0.6;

/** Desaturate an (r,g,b) toward its gray so the stage tint stays subtle. */
function muteRgb(r: number, g: number, b: number): [number, number, number] {
  const avg = (r + g + b) / 3;
  const m = (x: number) => Math.round(x * (1 - MUTE) + avg * MUTE);
  return [m(r), m(g), m(b)];
}

/** Build a CoverTint (muted tint + dark deep variant) from raw rgb. */
function tintFromRgb(r: number, g: number, b: number): CoverTint {
  const [mr, mg, mb] = muteRgb(r, g, b);
  const deep = (n: number) => Math.round(n * 0.18);
  return { tint: `${mr},${mg},${mb}`, tintDeep: `${deep(mr)},${deep(mg)},${deep(mb)}` };
}

/** Fallback tint from a sodaTheme accent (hex "#rrggbb") or indigo. */
export function tintFromTheme(theme?: { accent?: string } | null): CoverTint {
  const hex = theme?.accent;
  if (!hex || !/^#?[0-9a-fA-F]{6}$/.test(hex)) return INDIGO;
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return tintFromRgb(r, g, b);
}

/** Canvas-sample the cover's average color → tint. Any failure → fallback. */
export function extractCoverTint(url: string, fallback: CoverTint): Promise<CoverTint> {
  return new Promise((resolve) => {
    if (!url || typeof document === 'undefined') return resolve(fallback);
    try {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        try {
          const c = document.createElement('canvas');
          c.width = 16; c.height = 16;
          const ctx = c.getContext('2d');
          if (!ctx) return resolve(fallback);
          ctx.drawImage(img, 0, 0, 16, 16);
          const d = ctx.getImageData(0, 0, 16, 16).data;
          let r = 0, g = 0, b = 0, n = 0;
          for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; n++; }
          r = Math.round(r / n); g = Math.round(g / n); b = Math.round(b / n);
          resolve(tintFromRgb(r, g, b));
        } catch { resolve(fallback); }
      };
      img.onerror = () => resolve(fallback);
      img.src = url;
    } catch { resolve(fallback); }
  });
}
