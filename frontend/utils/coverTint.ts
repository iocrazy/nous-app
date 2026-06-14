export interface CoverTint { tint: string; tintDeep: string }

const INDIGO: CoverTint = { tint: '99,102,241', tintDeep: '24,28,46' };

/** Fallback tint from a sodaTheme accent (hex "#rrggbb") or indigo. */
export function tintFromTheme(theme?: { accent?: string } | null): CoverTint {
  const hex = theme?.accent;
  if (!hex || !/^#?[0-9a-fA-F]{6}$/.test(hex)) return INDIGO;
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  const deep = (n: number) => Math.round(n * 0.18);
  return { tint: `${r},${g},${b}`, tintDeep: `${deep(r)},${deep(g)},${deep(b)}` };
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
          const deep = (x: number) => Math.round(x * 0.18);
          resolve({ tint: `${r},${g},${b}`, tintDeep: `${deep(r)},${deep(g)},${deep(b)}` });
        } catch { resolve(fallback); }
      };
      img.onerror = () => resolve(fallback);
      img.src = url;
    } catch { resolve(fallback); }
  });
}
