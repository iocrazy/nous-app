/**
 * Canvas-core domain types (Phase 1 of canvas + AI upgrade).
 *
 * Mirror of `backend/app/schemas/canvas.py`. Snowflake bigint IDs come
 * over the wire as strings to preserve precision — never re-cast to
 * `number`.
 */

import type { CanvasRow } from '../../types/api';

// 'character' (mig 357) reuses the smart pipeline with the CharacterNode +
// preset agent workflow on top (character canvas epic).
// 'classic' (canvas 1.0 engine) is RETIRED: no new classic canvases can be
// created and the engine is gone, but the kind stays in the union because
// soft-deleted rows in the trash still serialize with it.
// 'storyboard' (shot-nodes-on-canvas epic Task 1, mig 421): one system
// per-episode canvas (`episode_id` set, get-or-create via
// `GET /canvases/storyboard`), never user-creatable — see
// `CreatableCanvasKind` below. Renders the same smart surface; Task 4's
// `reconcileShotNodes` orchestration in `CanvasPage.tsx` only runs for
// this kind.
export type CanvasKind =
  | 'smart'
  | 'lite'
  | 'classic'
  | 'character'
  | 'location'
  | 'prop'
  // 'costume' (mig 446) has been legal in the DB CHECK since the asset
  // library's P0 and was the one value neither enum carried — so a costume
  // asset's "Open In Canvas" silently downgraded to 'smart'. P4 ruling G.
  | 'costume'
  | 'storyboard';

/** Kinds a NEW canvas may be created with (mirror of backend
 *  `CreatableCanvasKind`) — everything except the retired 'classic' AND
 *  'storyboard' (system-created only, via `GET /canvases/storyboard`'s
 *  get-or-create — never `POST /projects/{id}/canvases`, matching the
 *  backend schema's deliberate exclusion, task-1-report.md). */
export type CreatableCanvasKind = Exclude<CanvasKind, 'classic' | 'storyboard'>;

/** kinds that render the smart surface (composer, smart node set, generation
 *  pipeline). 'character' is smart + CharacterNode + a preset workflow.
 *  Accepts null (store kind before load) for call-site convenience. */
export const isSmartFamily = (kind: CanvasKind | null | undefined): boolean =>
  kind === 'smart' ||
  kind === 'lite' ||
  kind === 'character' ||
  kind === 'location' ||
  kind === 'prop' ||
  // 'costume' MUST be here, not just in the enum: `CanvasSurface` passes
  // `nodeTypes` only for the smart family, and a kind outside it renders
  // every node as React Flow's default — the blank-canvas failure class.
  kind === 'costume';

/**
 * The library-entity canvas kinds — the four an asset can open a board for.
 *
 * `costume` belongs here (plan ruling G): `canvasKindFor` returns it, so the
 * pre-P4 three-way split answered `false` for a costume board while every
 * other part of the codebase treated the four alike. It was latent rather than
 * broken only because the entity seeding templates that read this were deleted;
 * `CanvasPage` now uses it to decide who gets a node bar, which makes it
 * load-bearing again.
 */
export type EntityCanvasKind = 'character' | 'location' | 'prop' | 'costume';
export const isEntityCanvas = (
  kind: CanvasKind | null | undefined,
): kind is EntityCanvasKind =>
  kind === 'character' ||
  kind === 'location' ||
  kind === 'prop' ||
  kind === 'costume';

export interface CanvasViewport {
  x: number;
  y: number;
  zoom: number;
}

/** Opaque JSONB pass-through — the React Flow layer owns the actual shape. */
export type CanvasNode = Record<string, unknown>;
export type CanvasConnection = Record<string, unknown>;
export type CanvasNodeOp = Record<string, unknown>;
export type CanvasConnectionOp = Record<string, unknown>;

/**
 * A full canvas document — the wire `CanvasRow` (generated from the backend
 * schema, see `types/api.ts`) with `viewport_json` narrowed from an opaque
 * object to the viewport the React Flow layer reads and writes.
 *
 * - `asset_id` (mig 446) is set by the asset sheet's "Open In Canvas";
 *   `episode_id` only on a `kind==='storyboard'` row (mig 421). Both are
 *   null otherwise — the backend always sends the key.
 * - `can_edit`: may THIS caller write the canvas? The same verdict the PUT's
 *   write guard reaches, shipped with the read so the surface can render
 *   read-only up front (see `canvasCoreStore.applyServerRow`). Only the two
 *   LOAD endpoints (`GET /canvases/{id}`, `GET /canvases/storyboard`) send
 *   it; create and save omit the key, and a Supabase Realtime row
 *   (`applyRemoteUpdate`) has no notion of a caller. `undefined` therefore
 *   means "this payload carries no permission statement", NOT "false".
 */
export type Canvas = Omit<CanvasRow, 'viewport_json'> & {
  viewport_json: CanvasViewport;
};

/** `GET /projects/{id}/canvases` row: summary columns and `node_count` — the
 *  list never carries the node graph. */
export type { CanvasSummary } from '../../types/api';

export interface CanvasCreatePayload {
  name?: string;
  kind?: CreatableCanvasKind;
  viewport_json?: CanvasViewport;
  /**
   * The asset this canvas belongs to (`canvases.asset_id`, mig 446). The
   * backend's `CanvasCreate` has accepted it since asset-library P2 Task 1 and
   * validates that the asset is readable from the project's asset scope
   * (404 otherwise); it was missing here, so the one caller that sends it -
   * the entity sheet's "Open in canvas" - had no way to type the field.
   */
  asset_id?: string;
}

export interface CanvasUpdatePayload {
  base_updated_at: string;
  name?: string;
  kind?: CanvasKind;
  viewport_json?: CanvasViewport;
  nodes_json?: CanvasNode[];
  connections_json?: CanvasConnection[];
  node_ops_json?: CanvasNodeOp[];
  connection_ops_json?: CanvasConnectionOp[];
}

/**
 * Discriminated union returned by `canvasService.save` so the caller
 * can pattern-match on the 409 case without a try/catch dance.
 */
export type CanvasSaveResult =
  | { ok: true; canvas: Canvas }
  | { ok: false; conflict: Canvas };
