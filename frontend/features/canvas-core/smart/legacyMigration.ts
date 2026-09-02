/**
 * Turning pre-P3 entity cards into asset cards, on load (P4 Task 6).
 *
 * A canvas saved before the asset library holds `character` / `location` /
 * `prop` nodes bound to `_legacy_project_characters` / `_legacy_project_lib_entities`
 * row ids (mig 447 renamed those tables; the DROP is P6). Those ids are NOT
 * `assets.id` and nothing downstream may treat one as if it were — which is why
 * the mapping is a server question (`GET /assets/resolve-legacy`) and not a
 * guess made here.
 *
 * Four rules, each with an obvious wrong version:
 *
 *  * A HIT REPLACES THE NODE IN PLACE. Same node id, same position, same
 *    parent — only `type` and `data` change. Every edge on the canvas points at
 *    node ids, so a new node with a new id would silently orphan the card's
 *    connections; that is the whole reason this is a rewrite and not a
 *    delete-and-add.
 *  * A MISS IS A BADGE, NOT A DELETION AND NOT A GUESS. `{"asset_id": null}` is
 *    a real 200 with two documented causes — the entity's project never
 *    migrated, or the migration ADOPTED a hand-made asset (adoption writes no
 *    `attrs.legacy_ids` by design). Neither is recoverable from the client, and
 *    matching on name would rewire a card to an asset nobody chose. The card
 *    stays exactly as it is and says `Unmigrated` out loud.
 *  * A FAILED REQUEST CHANGES NOTHING AT ALL. "Could not ask" is not "there is
 *    no asset". Marking a card unmigrated because the network blinked would
 *    write a claim nobody checked into `nodes_json` — the same distinction
 *    `AssetNodeView` draws between a 404 and everything else.
 *  * NOTHING BLOCKS THE FIRST PAINT. The caller runs this AFTER the document is
 *    in the store, so the legacy cards render first and swap when the answers
 *    arrive. A canvas that waits on N round trips before drawing anything is
 *    the blank-canvas class of failure with a different cause.
 */

import type { LegacyEntityKind } from '../../../services/assetsService';
import type { CanvasNode } from '../types';
import type { AssetNodeSeed } from './assetFiles';
import { assetNodeData } from './factories';

/** The three node types that can carry a legacy binding. */
export const LEGACY_NODE_TYPES: readonly LegacyEntityKind[] = [
  'character',
  'location',
  'prop',
];

/**
 * How many resolves are in flight at once.
 *
 * A board can hold a dozen legacy cards and each is one small request; firing
 * them all at once is a burst against an endpoint every other card on the
 * canvas is also using (each asset card fetches its own detail on mount).
 */
export const RESOLVE_CONCURRENCY = 4;

export interface LegacyCard {
  nodeId: string;
  kind: LegacyEntityKind;
  /** The legacy row id, as a string — Snowflakes never ride as JS numbers. */
  legacyId: string;
}

function nodeId(node: CanvasNode): string | null {
  const id = (node as { id?: unknown }).id;
  return typeof id === 'string' && id !== '' ? id : null;
}

/** The legacy binding on a node, or null when it is unbound / not a legacy
 *  card. A hand-placed card (`character_id: null`) has nothing to resolve. */
function legacyIdOf(node: CanvasNode, kind: LegacyEntityKind): string | null {
  const data = (node as { data?: Record<string, unknown> }).data;
  if (!data || typeof data !== 'object') return null;
  // `character` cards spell it `character_id`; `location`/`prop` share
  // `entity_id`, because they were rows of one table. Both spellings are the
  // persisted document's, so neither can be normalized away here.
  const raw = kind === 'character' ? data.character_id : data.entity_id;
  if (typeof raw === 'string' && raw !== '') return raw;
  // A document written by hand (or by an older serializer) can hold the id as
  // a JSON number. Accepting it is safe — the value goes straight back out as
  // a query-string parameter — and refusing would leave that card unmigrated
  // for a reason no UI could explain.
  if (typeof raw === 'number' && Number.isFinite(raw)) return String(raw);
  return null;
}

/** Every legacy card on the canvas that still has something to resolve. */
export function legacyCards(nodes: readonly CanvasNode[]): LegacyCard[] {
  const out: LegacyCard[] = [];
  for (const node of nodes) {
    const type = (node as { type?: unknown }).type;
    if (typeof type !== 'string') continue;
    if (!LEGACY_NODE_TYPES.includes(type as LegacyEntityKind)) continue;
    const id = nodeId(node);
    if (!id) continue;
    const legacyId = legacyIdOf(node, type as LegacyEntityKind);
    if (!legacyId) continue;
    out.push({ nodeId: id, kind: type as LegacyEntityKind, legacyId });
  }
  return out;
}

/** The same node, now an asset card. Id, position, parent and size survive. */
export function toAssetNode(node: CanvasNode, asset: AssetNodeSeed): CanvasNode {
  return {
    ...node,
    type: 'asset',
    // A loadout is not recoverable from a legacy card — it had no such
    // concept — so the card arrives generic and the user picks an outfit.
    data: assetNodeData(asset, null),
  };
}

/** The same node, flagged so the view can say `Unmigrated`. */
export function markUnmigrated(node: CanvasNode): CanvasNode {
  const data = (node as { data?: Record<string, unknown> }).data;
  return {
    ...node,
    data: { ...(data && typeof data === 'object' ? data : {}), unmigrated: true },
  };
}

export interface MigrationDeps {
  resolve: (kind: LegacyEntityKind, legacyId: string) => Promise<string | null>;
  fetchDetail: (assetId: string) => Promise<AssetNodeSeed>;
  concurrency?: number;
}

/**
 * One card's answer.
 *
 * Verdicts are deliberately SEPARATE from applying them. The resolves take
 * network time, during which the user can drag a node, type in a card, or
 * delete one — writing back a node list computed from the pre-request snapshot
 * would silently undo whatever they did. So the async half produces verdicts
 * keyed by node id, and the sync half applies them to whatever the node list
 * is at the moment of the write.
 */
export type LegacyVerdict =
  | { nodeId: string; kind: 'migrated'; asset: AssetNodeSeed }
  | { nodeId: string; kind: 'unmigrated' }
  | { nodeId: string; kind: 'unresolved' };

export interface MigrationOutcome {
  /** The node list with hits rewritten and misses flagged. Identical to the
   *  input array (by reference) when nothing changed. */
  nodes: CanvasNode[];
  /** Node ids that became asset cards. */
  migrated: string[];
  /** Node ids the server has no asset for. */
  unmigrated: string[];
  /** Node ids left untouched because a request failed — NOT a verdict about
   *  whether an asset exists. */
  unresolved: string[];
  /** Nothing about the node list changed, so the caller can skip the write. */
  unchanged: boolean;
}

async function verdictFor(
  card: LegacyCard,
  deps: MigrationDeps,
): Promise<LegacyVerdict> {
  let assetId: string | null;
  try {
    assetId = await deps.resolve(card.kind, card.legacyId);
  } catch (err) {
    console.error('[legacyMigration] resolve-legacy failed:', card, err);
    return { nodeId: card.nodeId, kind: 'unresolved' };
  }
  if (assetId === null) return { nodeId: card.nodeId, kind: 'unmigrated' };
  try {
    return {
      nodeId: card.nodeId,
      kind: 'migrated',
      asset: await deps.fetchDetail(assetId),
    };
  } catch (err) {
    // The mapping exists but the asset could not be read. Leaving the legacy
    // card alone is right: it is neither migrated nor provably unmigrated.
    console.error('[legacyMigration] detail fetch failed for asset', assetId, err);
    return { nodeId: card.nodeId, kind: 'unresolved' };
  }
}

/** Resolve `items` with at most `limit` in flight, preserving input order. */
async function mapWithLimit<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const out = new Array<R>(items.length);
  let next = 0;
  const worker = async (): Promise<void> => {
    for (;;) {
      const index = next;
      next += 1;
      if (index >= items.length) return;
      out[index] = await fn(items[index]);
    }
  };
  await Promise.all(
    Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, worker),
  );
  return out;
}

/**
 * Ask the server about every legacy card on the canvas.
 *
 * Never throws and never touches the document: a request that fails becomes an
 * `unresolved` verdict, which {@link applyLegacyVerdicts} treats as "change
 * nothing".
 */
export async function resolveLegacyVerdicts(
  nodes: readonly CanvasNode[],
  deps: MigrationDeps,
): Promise<LegacyVerdict[]> {
  const cards = legacyCards(nodes);
  if (cards.length === 0) return [];
  return mapWithLimit(cards, deps.concurrency ?? RESOLVE_CONCURRENCY, (card) =>
    verdictFor(card, deps),
  );
}

/**
 * Apply verdicts to a node list — the LIVE one, not the snapshot they were
 * computed from (see {@link LegacyVerdict}).
 *
 * A verdict naming a node that is no longer there is simply not applied; the
 * user deleted the card while the request was in flight, and re-adding it
 * would be the worst possible outcome of a migration.
 */
export function applyLegacyVerdicts(
  nodes: readonly CanvasNode[],
  verdicts: readonly LegacyVerdict[],
): MigrationOutcome {
  const byNode = new Map(verdicts.map((verdict) => [verdict.nodeId, verdict]));
  const migrated: string[] = [];
  const unmigrated: string[] = [];
  const unresolved: string[] = [];

  const next = nodes.map((node) => {
    const id = nodeId(node);
    const verdict = id ? byNode.get(id) : undefined;
    if (!verdict || !id) return node;
    if (verdict.kind === 'migrated') {
      migrated.push(id);
      return toAssetNode(node, verdict.asset);
    }
    if (verdict.kind === 'unresolved') {
      unresolved.push(id);
      return node;
    }
    unmigrated.push(id);
    // Already flagged by an earlier pass — return the SAME object so an idle
    // canvas cannot report a change (and schedule a write) on every load.
    const already = (node as { data?: { unmigrated?: unknown } }).data?.unmigrated;
    return already === true ? node : markUnmigrated(node);
  });

  const unchanged = next.every((node, i) => node === nodes[i]);
  return {
    nodes: unchanged ? (nodes as CanvasNode[]) : next,
    migrated,
    unmigrated,
    unresolved,
    unchanged,
  };
}
