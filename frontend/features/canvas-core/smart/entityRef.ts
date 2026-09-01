// features/canvas-core/smart/entityRef.ts
//
// CC5 asset backlink — derive which library entity (character / location /
// prop card) a prompt's generation belongs to, by walking the canvas graph
// upstream from the prompt. The ref is stamped into the dispatch `params`
// and lands verbatim in generated_media.params, which is what the library
// asset strips query. BFS from the prompt so the NEAREST bound card wins
// when branches merge.
//
// P4 OWNS THIS FILE'S MIGRATION. The ids it resolves are `project_characters`
// / `project_lib_entities` rows bound to canvas entity cards — the same legacy
// tables the workspace's Characters / Locations / Props / Costumes pages left
// behind in P3 (they read `assets` now). The card-side reader is already gone
// (P3 Task 6 retired `EntityAssetStrip`), so this stamp currently has no
// in-app consumer; re-pointing entity cards at `assets` / `canvas_asset_refs`
// is P4 canvas work, deliberately not done here.

import type { CanvasConnection, CanvasNode } from '../types';

export interface EntityRef {
  kind: 'character' | 'location' | 'prop';
  id: string;
}

const ENTITY_TYPES = new Set(['character', 'location', 'prop']);

const asObj = (n: unknown) => n as Record<string, unknown>;

function entityRefOf(node: CanvasNode): EntityRef | null {
  const type = String(asObj(node).type ?? '');
  if (!ENTITY_TYPES.has(type)) return null;
  const data = (asObj(node).data ?? {}) as Record<string, unknown>;
  // Character cards bind project_characters via character_id; location/prop
  // cards bind project_lib_entities via entity_id. Unbound (hand-placed)
  // cards have a null id and carry no ownership.
  const raw = type === 'character' ? data.character_id : data.entity_id;
  if (raw === null || raw === undefined || String(raw) === '') return null;
  return { kind: type as EntityRef['kind'], id: String(raw) };
}

/** Nearest bound entity card feeding (transitively) into `promptId`, or null. */
export function resolveEntityRef(
  promptId: string,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): EntityRef | null {
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
      const ref = entityRefOf(node);
      if (ref) return ref;
      next.push(...(upstreamOf.get(id) ?? []));
    }
    frontier = next;
  }
  return null;
}
