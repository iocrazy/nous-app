// Snapshot a hotspot into a note's ref_hotspot (save-as-note loop, spec §2 #6).
// The frontend RefHotspot shape IS the contract — the backend column is free
// JSONB. Optionals are omitted when absent (never emitted as null).
import type { RefHotspot } from '../../services/inspirationService';
import type { Hotspot } from '../../services/topicService';

export function hotspotToRef(h: Hotspot): RefHotspot {
  const ref: RefHotspot = { hotspot_id: h.id, title: h.title };
  const source = h.source_label ?? undefined;
  const url = h.origin_url || h.url || undefined;
  if (source) ref.source = source;
  if (typeof h.heat === 'number') ref.heat = h.heat;
  if (url) ref.url = url;
  if (h.captured_at) ref.captured_at = h.captured_at;
  return ref;
}

// Save-as-note prefill (spec P4d): turn the hotspot's category + tags into a
// leading `#tag` line so the user's own words land above it — two leading
// newlines push the tag line below wherever the cursor starts typing.
export function buildPrefillContent(h: Hotspot): string {
  const tags: string[] = [];
  const push = (raw?: string | null) => {
    const t = (raw ?? '').trim().toLowerCase().replace(/\s+/g, '-');
    if (t && !tags.includes(t)) tags.push(t);
  };
  push(h.category);
  for (const t of h.tags ?? []) push(t);
  return tags.length ? `\n\n${tags.map((t) => `#${t}`).join(' ')}` : '';
}
