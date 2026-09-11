// features/canvas-core/smart/mediaEditBridge.ts
// B3: media-card items are generated_media rows. genIdFromDurableUrl parses
// the row id out of the durable url shape
// (/api/v1/generated-media/{id}/file|cover|stream); null for any other url.

const DURABLE_RE = /\/api\/v1\/generated-media\/(\d+)\/(?:file|cover|stream)\b/;

export function genIdFromDurableUrl(url: string): string | null {
  const m = DURABLE_RE.exec(url);
  return m ? m[1] : null;
}
