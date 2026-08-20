// features/canvas-core/smart/dropUrl.ts
//
// IC parity (smartDropTextFragments / resolveSmartImageDropPayload): drags
// that arrive WITHOUT File objects — from another browser tab, an asset
// manager, a chat app — still carry the image as a URL in text/uri-list,
// text/html or text/plain. Parse those into fetchable candidates.

const URL_RE = /^https?:\/\/\S+$/i;

export function extractDropUrls(dt: DataTransfer): string[] {
  const out: string[] = [];
  const push = (u: string) => {
    const url = u.trim();
    if (URL_RE.test(url) && !out.includes(url)) out.push(url);
  };
  const types = Array.from(dt.types ?? []);
  if (types.includes('text/uri-list')) {
    for (const line of dt.getData('text/uri-list').split(/\r?\n/)) {
      if (!line.startsWith('#')) push(line);
    }
  }
  if (types.includes('text/html')) {
    const html = dt.getData('text/html');
    // <img src> first (the dragged picture itself), then <a href> fallback.
    for (const re of [/<img[^>]+src=["']([^"']+)["']/gi, /<a[^>]+href=["']([^"']+)["']/gi]) {
      let m: RegExpExecArray | null;
      while ((m = re.exec(html))) push(m[1]);
    }
  }
  if (types.includes('text/plain')) push(dt.getData('text/plain'));
  return out;
}

/** Fetch a dropped URL into a File. Null on network/CORS failure — the
 *  caller reports; many hosts simply don't allow cross-origin reads. */
export async function fetchUrlAsFile(url: string): Promise<File | null> {
  try {
    const res = await fetch(url, { mode: 'cors' });
    if (!res.ok) return null;
    const blob = await res.blob();
    if (!blob.type.startsWith('image/') && !blob.type.startsWith('video/')) return null;
    const name =
      decodeURIComponent(new URL(url).pathname.split('/').pop() || '') ||
      'dropped-image';
    return new File([blob], name, { type: blob.type });
  } catch (err) {
    console.error('[dropUrl] fetch failed for', url, err);
    return null;
  }
}
