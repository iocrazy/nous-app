// e2e/helpers/realShapes.ts
//
// Boundary-mock factories for scene/shot/canvas backend rows — REAL wire
// shape, not the frontend-normalized shape the app's services hand to React
// components.
//
// Event background (2026-08-12 production incident): a canvas self-heal
// regression shipped because `shot_id`/`scene_id` was treated as a STRING
// in one code path (the reconcile index, built off `Shot.id: string`) and a
// NUMBER in another (the actual `listShots`/`listScenes` HTTP response
// bodies — the shots/scenes REST routers return raw ORM dicts with no
// `str()` coercion; only `editor/sceneService.ts`'s `toShotDto`/`toSceneDoc`
// stringify at the frontend boundary via `String(row.id)`). The two never
// diverged in tests because EVERY hand-written fixture in the repo used
// idealized string ids (`'shot1'`, `'200'`) — no fixture ever exercised the
// numeric-JSON branch, so the mismatch shipped silently until it hit
// production canvases with real bigint rows (102 duplicated nodes for 6
// real shots — see e2e/canvas-dup-selfheal.spec.ts and
// features/canvas-core/ui/CanvasPage.remountDedupe.test.tsx for the full
// regression writeup).
//
// RULE (see CLAUDE.md 开发规范 "边界 mock 必须用真实 JSON 形状"): any mock
// that stands in for an HTTP response body — `page.route` fulfill(), a
// stubbed `fetch`, anything simulating what the wire actually carries — must
// reproduce the backend's real JSON shape, INCLUDING the type of each field
// (number vs string), not the shape frontend code would idealize it to. It
// is fine, and often correct, for a mock of an already-normalized frontend
// SERVICE function (e.g. `vi.mock('editor/sceneService')`'s `listShots`) to
// use strings, because that function's real implementation coerces ids to
// strings before returning — mocking it with numbers there would itself be
// unrealistic. The distinction is which boundary you're standing at: HTTP
// response body → real wire shape (this file); normalized service return
// value → the service's own documented contract.
//
// Verified against backend source (2026-08-11):
//   - scenes REST router (`script_scenes_router.py`) and shots REST router
//     (`script_shots_router.py`) return repository dicts with NO id
//     stringification — `id` / `scene_id` (FK) are native JSON *numbers*.
//   - canvases REST router (`canvases_router.py`) explicitly does
//     `out["id"] = str(out["id"])` (and project_id/episode_id/created_by) —
//     canvas rows ARE stringified. `realCanvasRow` below reflects that
//     asymmetry; don't "fix" it to match scenes/shots — they're genuinely
//     different on the wire.
//
// ID magnitude note: Snowflake bigints can in principle exceed
// Number.MAX_SAFE_INTEGER (2^53-1 = 9_007_199_254_740_991), which JSON
// numbers silently lose precision on. The ids below (~2.08e14, e.g.
// 208443000000001) are picked in the same range production canvases
// actually produced during the incident — comfortably under 2^53-1, so
// they round-trip exactly through JSON.stringify/parse while still being
// realistic 15-digit Snowflake values. If a test specifically needs to
// exercise IDs BEYOND 2^53 (the precision-loss danger zone itself), do not
// write a JS number literal for it — by the time it's a JS number the
// precision is already gone. Inject the raw JSON text instead so the
// boundary carries an un-roundtrippable literal exactly like a real
// Postgres bigint would:
//   route.fulfill({ body: `{"id":9223372036854775807,"scene_id":...}` })

/** Production-shaped scene row as returned by GET /api/v1/scripts/{id}/scenes. */
export interface RealSceneRow {
  id: number;
  script_id: number;
  chapter_id: number | null;
  heading_int_ext: string;
  location_text: string;
  time_of_day: string;
  content_version: number;
  content_json: unknown[];
  sort_order: number;
}

export interface RealSceneRowOptions {
  id?: number;
  scriptId?: number;
  chapterId?: number | null;
  headingIntExt?: string;
  locationText?: string;
  timeOfDay?: string;
  contentVersion?: number;
  contentJson?: unknown[];
  sortOrder?: number;
}

/**
 * A single scene row shaped exactly like `script_scenes_router.py`'s real
 * response: `id`, `script_id` and `chapter_id` are all JSON numbers, never
 * strings — `script_scene_repository.py`'s `_parity()` only stringifies
 * `uuid.UUID` and `datetime`/`date` fields; bigint ids/FKs stay native int
 * on read (its own in-file comment: "Ids/FKs stay native int on read (5.3
 * trap)"). Default id (208443000000100) matches the Snowflake range the
 * 2026-08-12 incident's production canvas actually carried.
 */
export function realSceneRow(opts: RealSceneRowOptions = {}): RealSceneRow {
  return {
    id: opts.id ?? 208443000000100,
    script_id: opts.scriptId ?? 208443000000400,
    chapter_id: opts.chapterId ?? null,
    heading_int_ext: opts.headingIntExt ?? 'INT',
    location_text: opts.locationText ?? 'House',
    time_of_day: opts.timeOfDay ?? 'DAY',
    content_version: opts.contentVersion ?? 1,
    content_json: opts.contentJson ?? [],
    sort_order: opts.sortOrder ?? 1,
  };
}

/** Production-shaped shot row as returned by GET /api/v1/scenes/{id}/shots. */
export interface RealShotRow {
  id: number;
  scene_id: number;
  shot_number: number;
  shot_type: string;
  camera_angle: string;
  camera_movement: string;
  focal_length: string;
  lighting: string | null;
  description: string;
  image_url: string | null;
  thumbnail_url: string | null;
  video_url: string | null;
  status: string;
  sort_order: number;
}

export interface RealShotRowOptions {
  id?: number;
  sceneId?: number;
  shotNumber?: number;
  shotType?: string;
  cameraAngle?: string;
  cameraMovement?: string;
  focalLength?: string;
  lighting?: string | null;
  description?: string;
  imageUrl?: string | null;
  thumbnailUrl?: string | null;
  videoUrl?: string | null;
  status?: string;
  sortOrder?: number;
}

/**
 * A single shot row shaped exactly like `script_shots_router.py`'s real
 * response: `id` AND `scene_id` (the FK) are JSON numbers — this is the
 * specific field the 2026-08-12 incident's reconcile index mis-typed as a
 * string. Default ids (208443000000001 / 208443000000100) match the same
 * production Snowflake range as {@link realSceneRow}'s default.
 */
export function realShotRow(opts: RealShotRowOptions = {}): RealShotRow {
  const shotNumber = opts.shotNumber ?? 1;
  return {
    id: opts.id ?? 208443000000001,
    scene_id: opts.sceneId ?? 208443000000100,
    shot_number: shotNumber,
    shot_type: opts.shotType ?? 'MEDIUM',
    camera_angle: opts.cameraAngle ?? 'EYE_LEVEL',
    camera_movement: opts.cameraMovement ?? 'STATIC',
    focal_length: opts.focalLength ?? '35mm',
    lighting: opts.lighting ?? null,
    description: opts.description ?? `Shot ${shotNumber}`,
    image_url: opts.imageUrl ?? null,
    thumbnail_url: opts.thumbnailUrl ?? null,
    video_url: opts.videoUrl ?? null,
    status: opts.status ?? 'empty',
    sort_order: opts.sortOrder ?? shotNumber * 1000,
  };
}

/**
 * `count` shot rows sharing one scene, with sequential ids starting at
 * `baseId` (default: {@link realShotRow}'s default id) — the common "N shots
 * in a scene" fixture shape, e.g. `realShotRows(6, { sceneId })` for a
 * 6-shot scene column.
 */
export function realShotRows(
  count: number,
  opts: { sceneId?: number; baseId?: number } = {},
): RealShotRow[] {
  const baseId = opts.baseId ?? 208443000000001;
  return Array.from({ length: count }, (_, i) =>
    realShotRow({ id: baseId + i, sceneId: opts.sceneId, shotNumber: i + 1 }),
  );
}

/**
 * Production-shaped canvas row as returned by GET /api/v1/canvases/{id} —
 * DELIBERATELY the odd one out: `canvases_router.py` stringifies `id` /
 * `project_id` / `episode_id` / `created_by`, unlike scenes/shots above.
 * `nodes_json`/`connections_json` are opaque JSONB the frontend authors
 * itself (React Flow node ids are always strings by React Flow's own
 * contract), so this factory only shapes the row's own columns.
 */
export interface RealCanvasRow {
  id: string;
  project_id: string | null;
  episode_id: string | null;
  name: string;
  kind: string;
  viewport_json: { x: number; y: number; zoom: number };
  nodes_json: unknown[];
  connections_json: unknown[];
  node_ops_json: unknown[];
  connection_ops_json: unknown[];
  base_updated_at: string;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export interface RealCanvasRowOptions {
  id?: string;
  projectId?: string | null;
  episodeId?: string | null;
  name?: string;
  kind?: string;
  viewportJson?: { x: number; y: number; zoom: number };
  nodesJson?: unknown[];
  connectionsJson?: unknown[];
  baseUpdatedAt?: string;
  createdAt?: string;
  updatedAt?: string;
  createdBy?: string | null;
}

export function realCanvasRow(opts: RealCanvasRowOptions = {}): RealCanvasRow {
  return {
    id: opts.id ?? '208443000009999',
    project_id: opts.projectId ?? '208443000000200',
    episode_id: opts.episodeId ?? '208443000000300',
    name: opts.name ?? 'EP1 · Storyboard',
    kind: opts.kind ?? 'storyboard',
    viewport_json: opts.viewportJson ?? { x: 0, y: 0, zoom: 1 },
    nodes_json: opts.nodesJson ?? [],
    connections_json: opts.connectionsJson ?? [],
    node_ops_json: [],
    connection_ops_json: [],
    base_updated_at: opts.baseUpdatedAt ?? '2020-01-02T00:00:00Z',
    created_at: opts.createdAt ?? '2020-01-01T00:00:00Z',
    updated_at: opts.updatedAt ?? '2020-01-02T00:00:00Z',
    created_by: opts.createdBy ?? null,
  };
}
