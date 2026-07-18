/**
 * API client for the v2 editor: episodes / scenes / anchored element ops.
 *
 * Data endpoints use the { success, data } envelope (unwrapResponse). The
 * ops endpoint speaks an If-Match optimistic-concurrency contract (PR #1052,
 * v0.25.126): the caller's expected content_version rides in the `If-Match`
 * header and the server answers 409 (version_conflict) / 422 (op rejected)
 * with typed bodies we surface as VersionConflictError / OpRejectedError.
 * convert-to-scenes is an async dispatch that returns a FLAT
 * { success, task_id } envelope (handleResponse), not { data } (#1019).
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from '../services/parserService';
import { handleResponse, unwrapResponse } from '../utils/apiHelpers';
import type { ElementOp, ScriptElement, SceneDoc } from './types';

const apiBase = () => `${getApiUrl()}/api/v1`;

/** 409 from the ops endpoint: someone else advanced the scene. */
export class VersionConflictError extends Error {
  constructor(
    public currentVersion: number,
    public elements: ScriptElement[],
  ) {
    super('version_conflict');
    this.name = 'VersionConflictError';
  }
}

/** 422 from the ops endpoint: an op could not be applied (e.g. missing_anchor). */
export class OpRejectedError extends Error {
  constructor(
    public code: string,
    public detail: unknown,
  ) {
    super(code);
    this.name = 'OpRejectedError';
  }
}

/**
 * Backend scene ROWS carry the element list as `content_json` (only the
 * elements/ops endpoint responds with `elements`). Normalize every row into
 * the SceneDoc shape here so no component ever sees the raw column name —
 * caught live on the first real-device pass (mocked fixtures used SceneDoc
 * directly and could never surface the mismatch).
 */
type SceneRow = Omit<SceneDoc, 'elements'> & {
  elements?: ScriptElement[] | null;
  content_json?: ScriptElement[] | null;
};

/**
 * Snowflake BIGINT ids come back as JSON *numbers*, but SceneDoc types them
 * (and the backend's move body validates them) as STRINGS. Leaving them numeric
 * bit twice, live: `moveScene({ before_scene_id | after_scene_id })` 422'd with
 * "Input should be a valid string", and every `=== someString` comparison
 * (e.g. activeSceneId, which comes from a `dataset.sceneId` attr and is always a
 * string) silently never matched. Coerce once, here at the boundary, so no
 * component ever sees a numeric id.
 */
function toSceneDoc(row: SceneRow): SceneDoc {
  const { content_json, elements, ...rest } = row;
  return {
    ...rest,
    id: String(rest.id),
    script_id: String(rest.script_id),
    chapter_id: rest.chapter_id == null ? null : String(rest.chapter_id),
    elements: elements ?? content_json ?? [],
  };
}

export async function listScenes(scriptId: string): Promise<SceneDoc[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/scenes`, { headers });
  const rows = await unwrapResponse<SceneRow[]>(res);
  return rows.map(toSceneDoc);
}

export async function getScene(sceneId: string): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}`, { headers });
  return toSceneDoc(await unwrapResponse<SceneRow>(res));
}

export async function createScene(
  scriptId: string,
  data: Partial<SceneDoc>,
): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/scenes`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return toSceneDoc(await unwrapResponse<SceneRow>(res));
}

export async function updateSceneMeta(
  sceneId: string,
  data: Partial<
    Pick<
      SceneDoc,
      'heading_int_ext' | 'location_text' | 'time_of_day' | 'position_x' | 'position_y'
    >
  >,
): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return toSceneDoc(await unwrapResponse<SceneRow>(res));
}

export async function deleteScene(sceneId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/scenes/${sceneId}`, { method: 'DELETE', headers });
}

/**
 * Persist a chapter node's canvas position (Phase B Task 3). PUTs the two
 * coordinates to the script-canvas chapter endpoint; the server passes them
 * straight through (ScriptChapterUpdate.position_x/y). Chapter ids are strings
 * end-to-end (Snowflake bigint) — never Number()-coerced.
 */
export async function updateChapterPosition(
  chapterId: string,
  pos: { position_x: number; position_y: number },
): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/projects/chapters/${chapterId}`, {
    method: 'PUT',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(pos),
  });
  // Surface a non-2xx (403/422/500) as a rejection so the caller's
  // `.catch(console.error)` fires instead of silently swallowing it.
  await unwrapResponse(res);
}

export async function applyOps(
  sceneId: string,
  ops: ElementOp[],
  expectedVersion: number,
): Promise<{ content_version: number; elements: ScriptElement[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/elements/ops`, {
    method: 'POST',
    headers: {
      ...headers,
      'Content-Type': 'application/json',
      'If-Match': String(expectedVersion),
    },
    body: JSON.stringify({ ops }),
  });
  if (res.status === 409) {
    const body = await res.json();
    throw new VersionConflictError(body.current_version, body.elements);
  }
  if (res.status === 422) {
    const body = await res.json();
    throw new OpRejectedError(body.code, body.detail);
  }
  return unwrapResponse<{ content_version: number; elements: ScriptElement[] }>(res);
}

export async function moveScene(
  sceneId: string,
  args: {
    chapter_id?: string | null;
    before_scene_id?: string | null;
    after_scene_id?: string | null;
  },
): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/move`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return unwrapResponse<SceneDoc>(res);
}

/**
 * An episode row. `script_count` (non-deleted scripts pointing at it) is
 * present on list responses so the UI can disable deleting a non-empty episode
 * — the episode_id FK is ON DELETE RESTRICT.
 */
export interface Episode {
  id: string;
  title: string;
  sort_order: number;
  script_count?: number;
}

export async function listEpisodes(projectId: string): Promise<Episode[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/projects/${projectId}/episodes`, { headers });
  return unwrapResponse<Episode[]>(res);
}

export async function createEpisode(
  projectId: string,
  data: { title?: string; sort_order?: number } = {},
): Promise<Episode> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/projects/${projectId}/episodes`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Episode>(res);
}

export async function updateEpisode(
  episodeId: string,
  data: { title?: string; sort_order?: number },
): Promise<Episode> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/episodes/${episodeId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Episode>(res);
}

export async function deleteEpisode(episodeId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/episodes/${episodeId}`, { method: 'DELETE', headers });
}

export async function convertToScenes(
  scriptId: string,
  chapterId: string,
): Promise<string> {
  const headers = await getAuthHeaders();
  const res = await fetch(
    `${apiBase()}/scripts/${scriptId}/chapters/${chapterId}/convert-to-scenes`,
    { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' } },
  );
  const body = await handleResponse<{ success: boolean; task_id: string }>(res);
  return body.task_id;
}

/** Client-generated element id so anchored ops can reference brand-new rows. */
export function newElementId(): string {
  return 'el_' + crypto.randomUUID().replace(/-/g, '').slice(0, 8);
}

// ---------------------------------------------------------------------------
// Shots (Phase B P3 — the storyboard tier that hangs off a scene)
// ---------------------------------------------------------------------------

/** A shot's status machine (server-owned; never client-set through updateShot). */
export type ShotStatus = 'empty' | 'generating' | 'done' | 'failed';

/**
 * A storyboard shot row. Every id is a string end-to-end (Snowflake bigint —
 * never Number()-coerced). The parameter tags + `description` are the only
 * client-editable fields via `updateShot`; `status` and the produced media URLs
 * flow exclusively through the generate workflow's write lane.
 */
export interface Shot {
  id: string;
  scene_id: string;
  shot_number: number | null;
  shot_type: string | null;
  camera_angle: string | null;
  camera_movement: string | null;
  focal_length: string | null;
  lighting: string | null;
  description: string | null;
  image_url: string | null;
  thumbnail_url: string | null;
  video_url: string | null;
  status: ShotStatus;
  sort_order: number;
}

/** Fields a caller may set when creating or editing a shot's tags/description. */
export type ShotInput = Partial<
  Pick<
    Shot,
    | 'shot_number'
    | 'shot_type'
    | 'camera_angle'
    | 'camera_movement'
    | 'focal_length'
    | 'lighting'
    | 'description'
    | 'sort_order'
  >
>;

/**
 * The generate endpoint answers 404 when `FEATURE_SHOT_GENERATE` is off (the
 * flag hides the endpoint's existence). We surface that as a distinct error so
 * the UI can degrade every Generate control for the session rather than treating
 * it like a transient failure.
 */
export class ShotGenerateDisabledError extends Error {
  constructor() {
    super('shot_generate_disabled');
    this.name = 'ShotGenerateDisabledError';
  }
}

export async function listShots(sceneId: string): Promise<Shot[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/shots`, { headers });
  return unwrapResponse<Shot[]>(res);
}

export async function getShot(shotId: string): Promise<Shot> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/shots/${shotId}`, { headers });
  return unwrapResponse<Shot>(res);
}

export async function createShot(sceneId: string, data: ShotInput = {}): Promise<Shot> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/shots`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Shot>(res);
}

export async function updateShot(shotId: string, data: ShotInput): Promise<Shot> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/shots/${shotId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Shot>(res);
}

export async function deleteShot(shotId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/shots/${shotId}`, { method: 'DELETE', headers });
}

export async function moveShot(
  shotId: string,
  args: { before_shot_id?: string | null; after_shot_id?: string | null },
): Promise<Shot> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/shots/${shotId}/move`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return unwrapResponse<Shot>(res);
}

// ── Beats (beat-sheet tier) ──────────────────────────────────────────────────

export interface Beat {
  id: string;
  script_id: string;
  title: string;
  summary: string | null;
  /** Ordered linked scene ids (Snowflake strings — never Number()-coerce). */
  scene_ids: string[];
  sort_order: number;
}

export interface BeatInput {
  title?: string;
  summary?: string | null;
  scene_ids?: string[];
}

/** Coerce scene ids to strings on write (#1006 — a bigint id must not round-trip
 *  as a JS number and lose precision / mismatch on read-back). */
function normalizeBeatInput(data: BeatInput): BeatInput {
  return data.scene_ids
    ? { ...data, scene_ids: data.scene_ids.map((id) => String(id)) }
    : data;
}

export async function listBeats(scriptId: string): Promise<Beat[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/beats`, { headers });
  return unwrapResponse<Beat[]>(res);
}

export async function createBeat(scriptId: string, data: BeatInput = {}): Promise<Beat> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/beats`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(normalizeBeatInput(data)),
  });
  return unwrapResponse<Beat>(res);
}

export async function updateBeat(beatId: string, data: BeatInput): Promise<Beat> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/beats/${beatId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(normalizeBeatInput(data)),
  });
  return unwrapResponse<Beat>(res);
}

export async function deleteBeat(beatId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/beats/${beatId}`, { method: 'DELETE', headers });
}

export async function moveBeat(
  beatId: string,
  args: { after_beat_id?: string | null },
): Promise<Beat> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/beats/${beatId}/move`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return unwrapResponse<Beat>(res);
}

/**
 * Dispatch the async AI storyboard breakdown of a scene. Returns the FLAT
 * { success, task_id } envelope (handleResponse) — the shots land some seconds
 * later, observed by polling `listShots` (#1019 flat dispatch contract).
 */
export async function autoStoryboard(sceneId: string): Promise<string> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/auto-storyboard`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
  });
  const body = await handleResponse<{ success: boolean; task_id: string }>(res);
  return body.task_id;
}

/**
 * Dispatch async single-shot image generation. Returns the FLAT
 * { success, task_id } envelope; the endpoint sets `status='generating'` before
 * dispatch and the workflow flips it to 'done' + image_url or 'failed'. A 404
 * means the feature flag is off → throws ShotGenerateDisabledError so the UI can
 * degrade globally (the guard runs before the flag check, so an access 403/404
 * is indistinguishable here and also correctly degrades — see router note).
 */
export async function generateShot(shotId: string): Promise<string> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/shots/${shotId}/generate`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
  });
  if (res.status === 404) throw new ShotGenerateDisabledError();
  const body = await handleResponse<{ success: boolean; task_id: string }>(res);
  return body.task_id;
}

/**
 * The generate-video endpoint answers 404 when `FEATURE_SHOT_VIDEO` is off. Its
 * own disabled error (separate from the image one) lets the UI degrade only the
 * video controls for the session while image Generate stays live.
 */
export class ShotVideoDisabledError extends Error {
  constructor() {
    super('shot_video_disabled');
    this.name = 'ShotVideoDisabledError';
  }
}

/**
 * Dispatch async single-shot video generation. Returns the FLAT
 * { success, task_id } envelope; unlike image generate the endpoint does NOT
 * flip shot.status (the video lifecycle lives in the Task Center), and the
 * workflow writes only `shot.video_url` on success — poll `getShot` until
 * `video_url` appears. A 404 means the flag is off → throws
 * ShotVideoDisabledError so the UI degrades the video controls globally.
 */
export async function generateShotVideo(shotId: string): Promise<string> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/shots/${shotId}/generate-video`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
  });
  if (res.status === 404) throw new ShotVideoDisabledError();
  const body = await handleResponse<{ success: boolean; task_id: string }>(res);
  return body.task_id;
}

// ---------------------------------------------------------------------------
// Version history (Phase B P4 — commit tags + diff + rollback over script_ops)
// ---------------------------------------------------------------------------

/**
 * The per-scene snapshot a commit stores in `scene_ids` — enough to render a
 * scene add/remove in a diff without a live fetch. Ids are strings end-to-end
 * (Snowflake bigint; never Number()-coerced).
 */
export interface SceneSnapshot {
  id: string;
  sort_order: number | null;
  heading_int_ext: string | null;
  location_text: string | null;
  /** Who last worked on this scene up to its watermark (uuid / 'copilot'). */
  author?: string | null;
}

/**
 * A version tag over the append-only op ledger: a per-scene op_seq watermark map
 * plus the scene-set snapshot at commit time. Content is NOT copied — the ledger
 * already holds it, so a diff replays it on demand.
 */
export interface ScriptCommit {
  id: string;
  script_id: string;
  message: string;
  watermarks: Record<string, number>;
  scene_ids: SceneSnapshot[];
  created_by: string;
  /** Server-resolved display name for `created_by` (null when unresolved). */
  author_name?: string | null;
  created_at: string;
}

export type DiffKind = 'added' | 'removed' | 'changed' | 'moved';

/** One element-level change within a scene diff (aligned by element id). */
export interface DiffChange {
  kind: DiffKind;
  id: string;
  before: ScriptElement | null;
  after: ScriptElement | null;
  /** Who last touched this element between the two watermarks (uuid / 'copilot'). */
  actor?: string | null;
}

/** The changes for one scene present on both sides of a diff. */
export interface SceneDiff {
  scene_id: string;
  elements: DiffChange[];
  /** Dominant author of the scene's changes (uuid / 'copilot'). */
  author?: string | null;
}

/**
 * The diff of one commit against another (or against the live 'current' state):
 * per-scene element changes, plus scene-set adds/removes. Only scenes with
 * changes appear in `scenes`. `authors` maps the actor uuids referenced across
 * the diff to display names (server-resolved; 'copilot' and the caller's own id
 * are left for the client to label).
 */
export interface CommitDiff {
  scenes: SceneDiff[];
  scenes_added: SceneSnapshot[];
  scenes_removed: SceneSnapshot[];
  authors?: Record<string, string>;
}

export type RollbackSceneStatus = 'unchanged' | 'rolled_back' | 'failed';

/** One scene's outcome in a rollback (a partial failure lists every scene). */
export interface RollbackSceneResult {
  scene_id: string;
  status: RollbackSceneStatus;
  error_code?: string;
  error?: string;
}

/**
 * A rollback result. `partial_failure` is true when at least one scene failed;
 * the endpoint still answers 200 (not an HTTP error) so the caller can surface
 * the per-scene detail and offer a retry. `not_deleted` are scenes created after
 * the commit (reported, not deleted); `not_resurrected` are scenes that existed
 * at commit time but are gone now (reported, not restored).
 */
export interface RollbackResult {
  commit_id: string;
  results: RollbackSceneResult[];
  not_deleted: string[];
  not_resurrected: string[];
  partial_failure: boolean;
}

export async function listCommits(scriptId: string): Promise<ScriptCommit[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/commits`, { headers });
  return unwrapResponse<ScriptCommit[]>(res);
}

export async function createCommit(
  scriptId: string,
  message: string,
): Promise<ScriptCommit> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/commits`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  return unwrapResponse<ScriptCommit>(res);
}

/**
 * Diff `commitId` against `against` (another commit id, or 'current' for the
 * live state). Returns the element-level scene changes plus scene-set adds.
 */
export async function diffCommit(
  scriptId: string,
  commitId: string,
  against: string = 'current',
): Promise<CommitDiff> {
  const headers = await getAuthHeaders();
  const res = await fetch(
    `${apiBase()}/scripts/${scriptId}/commits/${commitId}/diff?against=${encodeURIComponent(against)}`,
    { headers },
  );
  return unwrapResponse<CommitDiff>(res);
}

/**
 * Roll every scene back to its watermark in `commitId` (synchronous). A partial
 * failure is NOT an HTTP error — inspect `partial_failure` / `results` on the
 * returned object.
 */
export async function rollbackCommit(
  scriptId: string,
  commitId: string,
): Promise<RollbackResult> {
  const headers = await getAuthHeaders();
  const res = await fetch(
    `${apiBase()}/scripts/${scriptId}/commits/${commitId}/rollback`,
    {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
    },
  );
  return unwrapResponse<RollbackResult>(res);
}

export async function deleteCommit(commitId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/commits/${commitId}`, { method: 'DELETE', headers });
}
