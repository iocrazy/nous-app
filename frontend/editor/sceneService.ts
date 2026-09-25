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
import type {
  ScriptAiTaskDispatch,
  ScriptBeatWire,
  ScriptCommitDiffWire,
  ScriptCommitElementChangeWire,
  ScriptCommitRollback,
  ScriptCommitRollbackSceneResult,
  ScriptCommitSceneChange,
  ScriptCommitSceneDiffWire,
  ScriptCommitSceneSnapshot,
  ScriptCommitWire,
  ScriptSceneOpsResult,
  ScriptSceneVersionConflict,
  ScriptSceneWire,
  StoryboardShotWire,
  StoryboardTaskDispatch,
} from '../types/api';
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
 *
 * Ids: the scenes router stringifies them since #1809 (the wire type says
 * `string`). The `String()` coercions stay as a guard for a stale backend —
 * before #1809 they were JSON numbers, which 422'd `moveScene` anchors and
 * broke every `=== someString` comparison (e.g. activeSceneId).
 */
function toSceneDoc(row: ScriptSceneWire): SceneDoc {
  const { content_json, ...rest } = row;
  return {
    ...rest,
    id: String(rest.id),
    script_id: String(rest.script_id),
    chapter_id: rest.chapter_id == null ? null : String(rest.chapter_id),
    // JSONB passes through untyped (`unknown`); it is the element list.
    elements: (content_json as ScriptElement[] | null | undefined) ?? [],
  };
}

export async function listScenes(scriptId: string): Promise<SceneDoc[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/scenes`, {
    headers,
  });
  const rows = await unwrapResponse<ScriptSceneWire[]>(res);
  return rows.map(toSceneDoc);
}

export async function getScene(sceneId: string): Promise<SceneDoc> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}`, { headers });
  return toSceneDoc(await unwrapResponse<ScriptSceneWire>(res));
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
  return toSceneDoc(await unwrapResponse<ScriptSceneWire>(res));
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
  return toSceneDoc(await unwrapResponse<ScriptSceneWire>(res));
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
    const body = (await res.json()) as ScriptSceneVersionConflict;
    throw new VersionConflictError(
      body.current_version,
      body.elements as ScriptElement[],
    );
  }
  if (res.status === 422) {
    const body = await res.json();
    throw new OpRejectedError(body.code, body.detail);
  }
  const result = await unwrapResponse<ScriptSceneOpsResult>(res);
  return { ...result, elements: result.elements as ScriptElement[] };
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
  // The move route returns a raw row (`content_json`, not `elements`) like the
  // other row routes; it used to be returned un-normalized under a SceneDoc type.
  return toSceneDoc(await unwrapResponse<ScriptSceneWire>(res));
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
  const res = await fetch(`${apiBase()}/projects/${projectId}/episodes`, {
    headers,
  });
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
  await fetch(`${apiBase()}/episodes/${episodeId}`, {
    method: 'DELETE',
    headers,
  });
}

export async function convertToScenes(
  scriptId: string,
  chapterId: string,
): Promise<string> {
  const headers = await getAuthHeaders();
  const res = await fetch(
    `${apiBase()}/scripts/${scriptId}/chapters/${chapterId}/convert-to-scenes`,
    {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
    },
  );
  const body = await handleResponse<ScriptAiTaskDispatch>(res);
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
 * A storyboard shot row: the wire row with `status` narrowed to the four
 * states the server writes. Every id is a string end-to-end (Snowflake bigint —
 * never Number()-coerced). The parameter tags + `description` are the only
 * client-editable fields via `updateShot`; `status` and the produced media URLs
 * flow exclusively through the generate workflow's write lane.
 */
export type Shot = Omit<StoryboardShotWire, 'status'> & { status: ShotStatus };

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
 * Same boundary rule as `toSceneDoc` above. The shots router stringifies its
 * ids since #1809; before that `id` / `scene_id` arrived as JSON *numbers*,
 * which shipped the 2026-08-12 storyboard-canvas production bug (shotSync's
 * `reconcileShotNodes` indexes bound nodes with a `typeof shotId === 'string'`
 * narrow, so every mounted reconcile re-added every shot — duplicate
 * `shot-{id}` node ids, permanently `visibility:hidden`). The `String()`
 * coercions stay as a guard against a stale backend. `status` is a plain
 * string on the wire; the server only ever writes the four ShotStatus values.
 */
function toShot(row: StoryboardShotWire): Shot {
  return {
    ...row,
    id: String(row.id),
    scene_id: String(row.scene_id),
    status: row.status as ShotStatus,
  };
}

export async function listShots(sceneId: string): Promise<Shot[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/shots`, { headers });
  return (await unwrapResponse<StoryboardShotWire[]>(res)).map(toShot);
}

export async function createShot(sceneId: string, data: ShotInput = {}): Promise<Shot> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scenes/${sceneId}/shots`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return toShot(await unwrapResponse<StoryboardShotWire>(res));
}

export async function updateShot(shotId: string, data: ShotInput): Promise<Shot> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/shots/${shotId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return toShot(await unwrapResponse<StoryboardShotWire>(res));
}

// ── Beats (beat-sheet tier) ──────────────────────────────────────────────────

/**
 * A beat as the editor sees it: the wire row (`ScriptBeatWire`) with its
 * Snowflake `id` / `script_id` stringified at this boundary (`toBeat`). The
 * wire sends them as JSON numbers; left numeric, `moveBeat`'s `after_beat_id`
 * went out as a number and the backend (a `str` field) answered 422, so
 * drag-reordering in the list view always failed. `scene_ids` are already
 * strings; `start_sec` null = not yet arranged, `beat_role` null = free-form,
 * `color` null = neutral chrome.
 */
export type Beat = Omit<ScriptBeatWire, 'id' | 'script_id'> & {
  id: string;
  script_id: string;
};

function toBeat(row: ScriptBeatWire): Beat {
  return { ...row, id: String(row.id), script_id: String(row.script_id) };
}

export interface BeatInput {
  title?: string;
  summary?: string | null;
  scene_ids?: string[];
  start_sec?: number | null;
  duration_sec?: number | null;
  beat_role?: string | null;
  color?: string | null;
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
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/beats`, {
    headers,
  });
  return (await unwrapResponse<ScriptBeatWire[]>(res)).map(toBeat);
}

export async function createBeat(scriptId: string, data: BeatInput = {}): Promise<Beat> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/beats`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(normalizeBeatInput(data)),
  });
  return toBeat(await unwrapResponse<ScriptBeatWire>(res));
}

export async function updateBeat(beatId: string, data: BeatInput): Promise<Beat> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/beats/${beatId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(normalizeBeatInput(data)),
  });
  return toBeat(await unwrapResponse<ScriptBeatWire>(res));
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
  return toBeat(await unwrapResponse<ScriptBeatWire>(res));
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
  const body = await handleResponse<StoryboardTaskDispatch>(res);
  return body.task_id;
}

// ---------------------------------------------------------------------------
// Version history (Phase B P4 — commit tags + diff + rollback over script_ops)
// ---------------------------------------------------------------------------

/**
 * The per-scene snapshot a commit stores in `scene_ids`; in a diff's
 * `scenes_added` / `scenes_removed` it also carries the scene's last `author`.
 * Ids are strings end-to-end (Snowflake bigint; never Number()-coerced).
 */
export type SceneSnapshot = ScriptCommitSceneSnapshot &
  Partial<Pick<ScriptCommitSceneChange, 'author'>>;

/**
 * A version tag over the append-only op ledger, with its Snowflake `id` /
 * `script_id` stringified at this boundary (`toCommit`; the wire sends
 * numbers). `author_name` is only on list rows (server-resolved; null when
 * unresolved) — `createCommit`'s row has none.
 */
export type ScriptCommit = Omit<ScriptCommitWire, 'id' | 'script_id' | 'author_name'> & {
  id: string;
  script_id: string;
  author_name?: string | null;
};

function toCommit(
  row: Omit<ScriptCommitWire, 'author_name'> & { author_name?: string | null },
): ScriptCommit {
  return { ...row, id: String(row.id), script_id: String(row.script_id) };
}

/** One element-level change; `before` / `after` are the raw element dicts. */
export type DiffChange = Omit<ScriptCommitElementChangeWire, 'before' | 'after'> & {
  before: ScriptElement | null;
  after: ScriptElement | null;
};

export type DiffKind = DiffChange['kind'];

/** The changes for one scene present on both sides of a diff. */
export type SceneDiff = Omit<ScriptCommitSceneDiffWire, 'elements'> & {
  elements: DiffChange[];
};

/**
 * The diff of one commit against another (or against the live 'current' state).
 * `authors` maps actor uuids to display names ('copilot' and unresolved ids are
 * absent; the client labels those).
 */
export type CommitDiff = Omit<ScriptCommitDiffWire, 'scenes'> & {
  scenes: SceneDiff[];
};

/** One scene's outcome in a rollback; `error_code` / `error` only on `failed`. */
export type RollbackSceneResult = ScriptCommitRollbackSceneResult;
export type RollbackSceneStatus = RollbackSceneResult['status'];

/**
 * A rollback result. `partial_failure` is true when at least one scene failed;
 * the endpoint still answers 200 so the caller can surface the per-scene detail.
 */
export type RollbackResult = ScriptCommitRollback;

export async function listCommits(scriptId: string): Promise<ScriptCommit[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/commits`, {
    headers,
  });
  return (await unwrapResponse<ScriptCommitWire[]>(res)).map(toCommit);
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
  return toCommit(await unwrapResponse<Omit<ScriptCommitWire, 'author_name'>>(res));
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
  await fetch(`${apiBase()}/commits/${commitId}`, {
    method: 'DELETE',
    headers,
  });
}
