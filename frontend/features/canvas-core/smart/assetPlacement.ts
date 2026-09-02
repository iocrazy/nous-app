/**
 * Where asset cards go when something OTHER than a pointer places them
 * (P4 Task 6).
 *
 * Three entry points drop asset nodes without a cursor to aim at: the sheet's
 * "Send To Canvas" (one card onto a canvas the user is not even looking at),
 * "Insert Project Assets" (a whole project's shelf at once), and the empty
 * asset-bound canvas's seed. All three need the same two answers — where is
 * there room, and which assets are already here — so both live here rather
 * than being re-derived at each call site with slightly different arithmetic.
 *
 * "Room" is deliberately crude: everything new goes to the RIGHT of the
 * existing content's bounding box. It cannot overlap, it does not reflow a
 * board the user arranged by hand, and it is stable — running the same insert
 * twice puts the second batch beyond the first rather than on top of it. A
 * packing algorithm would be cleverer and would move things the user placed.
 */

import { ASSET_TYPES, type AssetType } from '../../../components/assets/assetSlots';
import type { AssetRow } from '../../../services/assetsService';
import type { CanvasNode } from '../types';
import type { AssetNodeSeed } from './assetFiles';
import { createAssetNode } from './factories';
import { SMART_NODE_DEFAULT_WIDTH, type SmartNodeType } from './types';

/** Horizontal gap between the existing content and the first new column. */
export const INSERT_GAP_X = 120;
/** Column pitch for a lane of asset cards. */
export const LANE_STEP_X = SMART_NODE_DEFAULT_WIDTH.asset + 60;
/** Row pitch inside one lane — the card is a cover + meta + checklist stack. */
export const LANE_STEP_Y = 300;

/** Fallback width for a node whose type is not in the smart table. */
const FALLBACK_WIDTH = 280;

export interface Position {
  x: number;
  y: number;
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** A node's rendered width, best effort. React Flow writes `width` once the
 *  node has been measured; before that the type's default is the only
 *  estimate there is, and an estimate beats treating the node as zero-wide. */
function nodeWidth(node: CanvasNode): number {
  const measured = readNumber((node as { width?: unknown }).width);
  if (measured !== null && measured > 0) return measured;
  const type = (node as { type?: unknown }).type;
  if (typeof type === 'string' && type in SMART_NODE_DEFAULT_WIDTH) {
    return SMART_NODE_DEFAULT_WIDTH[type as SmartNodeType];
  }
  return FALLBACK_WIDTH;
}

function nodePosition(node: CanvasNode): Position | null {
  const pos = (node as { position?: unknown }).position;
  if (!pos || typeof pos !== 'object') return null;
  const x = readNumber((pos as { x?: unknown }).x);
  const y = readNumber((pos as { y?: unknown }).y);
  if (x === null || y === null) return null;
  return { x, y };
}

/**
 * The first free spot to the right of everything already on the canvas.
 *
 * An empty canvas answers the origin, so a seeded card lands where the default
 * viewport already looks. A canvas whose nodes carry no readable position
 * (a hand-edited document) is treated as empty rather than throwing: putting
 * the card at the origin is recoverable, refusing to place it is not.
 */
export function nextFreePosition(nodes: readonly CanvasNode[]): Position {
  let maxRight: number | null = null;
  let minTop: number | null = null;
  for (const node of nodes) {
    const pos = nodePosition(node);
    if (!pos) continue;
    const right = pos.x + nodeWidth(node);
    maxRight = maxRight === null ? right : Math.max(maxRight, right);
    minTop = minTop === null ? pos.y : Math.min(minTop, pos.y);
  }
  if (maxRight === null || minTop === null) return { x: 0, y: 0 };
  return { x: maxRight + INSERT_GAP_X, y: minTop };
}

/** Every `assets.id` an asset card on this canvas already points at. */
export function assetIdsOnCanvas(nodes: readonly CanvasNode[]): Set<string> {
  const ids = new Set<string>();
  for (const node of nodes) {
    if ((node as { type?: unknown }).type !== 'asset') continue;
    const assetId = (node as { data?: { asset_id?: unknown } }).data?.asset_id;
    if (typeof assetId === 'string' && assetId !== '') ids.add(assetId);
  }
  return ids;
}

export interface LaneSlot<T> {
  item: T;
  position: Position;
  /** 0-based column, in {@link ASSET_TYPES} order. Exported for the tests and
   *  for a caller that wants to label the lanes. */
  lane: number;
  assetType: AssetType;
}

/**
 * One lane per asset type, laid out left to right from `origin`.
 *
 * The four the plan names — character / location / prop / costume — come first
 * because that is already {@link ASSET_TYPES}' order. `prompt` and `audio` are
 * NOT dropped: a project can link either, and silently placing four of six
 * types would be the "insert did less than it said" failure this repo keeps
 * re-learning. They simply get the fifth and sixth lanes.
 *
 * A type with no assets consumes NO horizontal space, so the common project
 * (characters, locations, props, costumes) really does render four adjacent
 * lanes rather than four lanes with gaps where the empty ones would have been.
 */
export function layoutAssetLanes<T extends { asset_type: AssetType }>(
  items: readonly T[],
  origin: Position,
): LaneSlot<T>[] {
  const byType = new Map<AssetType, T[]>();
  for (const item of items) {
    const bucket = byType.get(item.asset_type);
    if (bucket) bucket.push(item);
    else byType.set(item.asset_type, [item]);
  }
  const lanes = ASSET_TYPES.filter((type) => (byType.get(type)?.length ?? 0) > 0);
  const out: LaneSlot<T>[] = [];
  lanes.forEach((type, lane) => {
    (byType.get(type) ?? []).forEach((item, row) => {
      out.push({
        item,
        assetType: type,
        lane,
        position: {
          x: origin.x + lane * LANE_STEP_X,
          y: origin.y + row * LANE_STEP_Y,
        },
      });
    });
  });
  return out;
}

export interface ProjectAssetInsert {
  /** The new cards, ready to append. Empty when there was nothing to add. */
  nodes: CanvasNode[];
  inserted: number;
  /** Already on this canvas, so not placed again. */
  skipped: number;
}

/**
 * Lay a project's whole asset shelf onto a canvas, one lane per type.
 *
 * An asset already referenced by a card here is SKIPPED, not duplicated — the
 * insert is meant to be safe to press twice, and two cards for one asset would
 * both feed the same references into anything downstream.
 *
 * ⚠️ The cards are seeded from SUMMARY rows (`GET /projects/{id}/assets`),
 * which carry `file_counts_by_slot` — a tally — and not the file rows. So
 * `selected_file_ids` starts EMPTY, and each card's own checklist (which the
 * view fills from its own detail fetch) is where the user ticks what to
 * reference. That is stated rather than papered over: the alternative is one
 * `GET /assets/{id}` per asset before anything appears on screen, which for a
 * shelf of dozens is a long blank pause to pre-answer a question the user is
 * about to answer themselves. `AssetNodeSeed` documents this exact degraded
 * case ("a caller holding only a summary row still gets a valid node, just
 * with an empty selection").
 */
export function buildProjectAssetNodes(
  assets: readonly AssetRow[],
  existing: readonly CanvasNode[],
): ProjectAssetInsert {
  const present = assetIdsOnCanvas(existing);
  const fresh = assets.filter((asset) => !present.has(asset.id));
  const slots = layoutAssetLanes(fresh, nextFreePosition(existing));
  return {
    nodes: slots.map(
      (slot) =>
        createAssetNode(slot.item as AssetNodeSeed, { position: slot.position }) as CanvasNode,
    ),
    inserted: fresh.length,
    skipped: assets.length - fresh.length,
  };
}
