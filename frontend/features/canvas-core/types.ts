/**
 * Canvas-core domain types (Phase 1 of canvas + AI upgrade).
 *
 * Mirror of `backend/app/schemas/canvas.py`. Snowflake bigint IDs come
 * over the wire as strings to preserve precision — never re-cast to
 * `number`.
 */

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
  kind === 'prop';

/** The library-entity canvas kinds — each seeds its own preset workflow. */
export type EntityCanvasKind = 'character' | 'location' | 'prop';
export const isEntityCanvas = (
  kind: CanvasKind | null | undefined,
): kind is EntityCanvasKind =>
  kind === 'character' || kind === 'location' || kind === 'prop';

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

export interface Canvas {
  id: string;
  project_id: string;
  /** Owning episode for a `kind==='storyboard'` row (Task 1, mig 421);
   *  absent/null for every other kind. Snowflake bigint as string. */
  episode_id?: string | null;
  name: string;
  kind: CanvasKind;
  viewport_json: CanvasViewport;
  nodes_json: CanvasNode[];
  connections_json: CanvasConnection[];
  node_ops_json: CanvasNodeOp[];
  connection_ops_json: CanvasConnectionOp[];
  base_updated_at: string;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export interface CanvasCreatePayload {
  name?: string;
  kind?: CreatableCanvasKind;
  viewport_json?: CanvasViewport;
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
