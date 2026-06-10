/**
 * Canvas-core domain types (Phase 1 of canvas + AI upgrade).
 *
 * Mirror of `backend/app/schemas/canvas.py`. Snowflake bigint IDs come
 * over the wire as strings to preserve precision — never re-cast to
 * `number`.
 */

export type CanvasKind = 'smart' | 'classic';

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
  kind?: CanvasKind;
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
