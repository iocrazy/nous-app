// features/canvas-core/smart/assetRef.ts
//
// Generation provenance — which ASSET a prompt's output belongs to, derived by
// walking the canvas graph upstream from the prompt. The answer is stamped into
// the dispatch `params` and lands verbatim in `generated_media.params`, which is
// what "show me everything generated from this character" reads.
//
// BFS from the prompt so the NEAREST bound card wins when branches merge: a
// prompt fed by a character card two hops up and a location card five hops up
// belongs to the character.
//
// ─ What this replaced (asset-library P4 Task 5, plan ruling H) ──────────────
// This file used to resolve the OLD smart cards — `character_id` / `entity_id`,
// rows of `_legacy_project_characters` / `_legacy_project_lib_entities` (renamed
// by mig 447, DROP scheduled for P6) — and stamp them as `params.entity_kind` +
// `params.entity_id`. Both halves of that stamp are now gone: the reader
// (`fetchEntityGenerations`) had had no in-app caller since P3 Task 6, and
// keeping the writer alive would have kept emitting provenance that points at
// tables scheduled for deletion.
//
// Legacy cards are deliberately NOT resolved here any more. A `character_id` is
// not an `assets.id`, so answering with one would put the wrong table's key into
// `source_asset_id`. Re-pointing those cards at `assets` is Task 6; until a card
// is migrated it contributes no provenance, which is the honest answer — the
// alternative is a stamp that reads as an asset id and is not one.
//
// Historical rows keep the old spelling: the backfill maps
// `params.entity_kind`/`entity_id` → `source_asset_id` (see
// `_ENTITY_KIND_TO_LEGACY_TABLE` in `backfill_assets_from_project_entities`), so
// rows written before this change stay comparable with what it writes now.

import type { CanvasConnection, CanvasNode } from '../types';
import type { AssetNodeData } from './types';

export interface AssetRef {
  /** `assets.id` — Snowflake as a string. */
  asset_id: string;
  /** `asset_loadouts.id`, or null when the card binds no outfit. */
  loadout_id: string | null;
}

const asObj = (n: unknown) => n as Record<string, unknown>;

function assetRefOf(node: CanvasNode): AssetRef | null {
  if (String(asObj(node).type ?? '') !== 'asset') return null;
  const data = (asObj(node).data ?? {}) as Partial<AssetNodeData>;
  // A tombstoned card still shows its snapshot, but the asset row is gone —
  // stamping its id would point provenance at a deleted asset.
  if (data.removed === true) return null;
  const raw = data.asset_id;
  if (raw === null || raw === undefined || String(raw) === '') return null;
  return {
    asset_id: String(raw),
    loadout_id:
      data.loadout_id === null || data.loadout_id === undefined
        ? null
        : String(data.loadout_id),
  };
}

/** Nearest bound asset card feeding (transitively) into `promptId`, or null. */
export function resolveAssetRef(
  promptId: string,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): AssetRef | null {
  const byId = new Map(nodes.map((n) => [String(asObj(n).id), n]));
  const upstreamOf = new Map<string, string[]>();
  for (const c of connections) {
    const target = String(asObj(c).target);
    const source = String(asObj(c).source);
    const list = upstreamOf.get(target);
    if (list) list.push(source);
    else upstreamOf.set(target, [source]);
  }

  const visited = new Set<string>([promptId]);
  let frontier = upstreamOf.get(promptId) ?? [];
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const id of frontier) {
      if (visited.has(id)) continue;
      visited.add(id);
      const node = byId.get(id);
      if (!node) continue;
      const ref = assetRefOf(node);
      if (ref) return ref;
      next.push(...(upstreamOf.get(id) ?? []));
    }
    frontier = next;
  }
  return null;
}
