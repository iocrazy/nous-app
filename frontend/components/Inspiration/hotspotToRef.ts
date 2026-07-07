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
