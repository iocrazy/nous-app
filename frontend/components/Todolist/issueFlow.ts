/**
 * Stage-mirror flow progress — the single data source for the per-row / detail
 * flow-progress dot strip (Issues 专题 ②).
 *
 * A stage-mirror issue (`origin_kind='project_stage'`, origin_id
 * `project_stage:{projectId}:{stageId}`) is a project SOP stage projected into
 * the todo list. Its row shows the *project's* flow progress — where the project
 * currently sits in the global stage catalog — NOT the issue's own status.
 *
 * The flow is assembled from two existing endpoints (no backend change): the
 * GLOBAL stage catalog (`fetchStageCatalog`, one call serves every project) and
 * the per-project current stage (`fetchCurrentStage`). Both are cached at module
 * scope so a list of N mirror rows across M projects costs 1 + M requests, not
 * one per row. When the workflow session's node API lands, only the two loader
 * functions here change — the pure helpers and the components stay put.
 *
 * ⚠️ Snowflake ids are strings end-to-end — never Number() a project/stage id.
 */

import { useEffect, useMemo, useState } from 'react';

import type { ProjectStage } from '../../types';
import { fetchCurrentStage, fetchStageCatalog } from '../../services/projectsService';
import { parseOriginId } from './issueOrigin';

// ── origin parse ────────────────────────────────────────────────────────────

export interface StageMirrorRef {
  /** Snowflake project id, kept as a string. */
  projectId: string;
  /** Snowflake id of the stage this mirror issue represents. */
  stageId: string;
}

/**
 * Extract `{ projectId, stageId }` from a stage-mirror issue's origin, or null
 * when the issue is not a stage mirror or its origin_id is malformed. Reuses
 * the shared `parseOriginId` so the origin format lives in exactly one place.
 */
export function parseStageMirror(
  originKind: string | null | undefined,
  originId: string | null | undefined,
): StageMirrorRef | null {
  if (originKind !== 'project_stage') return null;
  const origin = parseOriginId(originId);
  if (!origin || origin.kind !== 'project_stage') return null;
  // origin.id is `{projectId}:{stageId}` — split on the FIRST colon, both halves
  // stay strings so Snowflake bigints survive intact.
  const colon = origin.id.indexOf(':');
  if (colon <= 0) return null;
  const projectId = origin.id.slice(0, colon);
  const stageId = origin.id.slice(colon + 1);
  if (!projectId || !stageId) return null;
  return { projectId, stageId };
}

// ── pure flow helpers ─────────────────────────────────────────────────────────

export type DotState = 'done' | 'current' | 'future';

/** Index of `stageId` within the ordered catalog, or -1 when absent. */
export function stageIndex(catalog: ProjectStage[], stageId: string): number {
  return catalog.findIndex((s) => String(s.id) === String(stageId));
}

/**
 * The dot states for a catalog of `total` stages given the current index:
 * every stage before it is done, the current one is current, the rest future.
 * A negative `currentIndex` (unknown stage) yields all-future dots.
 */
export function computeDots(total: number, currentIndex: number): DotState[] {
  return Array.from({ length: Math.max(0, total) }, (_, i) =>
    currentIndex >= 0 && i < currentIndex
      ? 'done'
      : i === currentIndex
        ? 'current'
        : 'future',
  );
}

export interface FlowPosition {
  /** 1-based position of the current stage (0 when unknown). */
  x: number;
  /** Total number of stages. */
  y: number;
}

export function flowPosition(total: number, currentIndex: number): FlowPosition {
  return { x: currentIndex >= 0 ? currentIndex + 1 : 0, y: Math.max(0, total) };
}

// ── due-date bucketing (pre-baked; the column lands with the workflow session) ─

export type DueKind = 'normal' | 'soon' | 'overdue';

export interface DueInfo {
  kind: DueKind;
  label: string;
}

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

function monthDay(d: Date): string {
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Bucket an issue's due date for display, or null to render nothing. Returns
 * null when `dueDate` is absent — the `due_date` column does not exist yet
 * (it arrives with the workflow session's migration), so every current payload
 * simply omits it and the UI stays blank until then. Also null on an unparseable
 * value rather than throwing.
 *
 * Buckets: overdue (past) → rose "Overdue · Jul 17"; soon (within 24h) → amber
 * "Due tomorrow"; otherwise normal grey "Jul 24".
 */
export function dueBucket(
  dueDate: string | null | undefined,
  now: Date = new Date(),
): DueInfo | null {
  if (!dueDate) return null;
  const due = new Date(dueDate);
  if (Number.isNaN(due.getTime())) return null;
  const delta = due.getTime() - now.getTime();
  if (delta < 0) return { kind: 'overdue', label: `Overdue · ${monthDay(due)}` };
  if (delta <= DAY_MS) return { kind: 'soon', label: 'Due tomorrow' };
  return { kind: 'normal', label: monthDay(due) };
}

/** Read a due date off a raw issue row without assuming the column exists. */
export function readDueDate(raw: unknown): string | null | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const value = (raw as Record<string, unknown>).due_date;
  return typeof value === 'string' ? value : undefined;
}

// ── cached data source (swap these two when the node API lands) ────────────────

let catalogPromise: Promise<ProjectStage[]> | null = null;
const currentStagePromises = new Map<string, Promise<ProjectStage | null>>();

/** Load the global stage catalog once and memoise it (retryable on failure). */
export function loadStageCatalog(): Promise<ProjectStage[]> {
  if (!catalogPromise) {
    catalogPromise = fetchStageCatalog().catch((err) => {
      catalogPromise = null; // let a later render retry
      throw err;
    });
  }
  return catalogPromise;
}

/** Load a project's current stage once per project and memoise it. */
export function loadCurrentStage(projectId: string): Promise<ProjectStage | null> {
  let promise = currentStagePromises.get(projectId);
  if (!promise) {
    promise = fetchCurrentStage(projectId).catch((err) => {
      currentStagePromises.delete(projectId);
      throw err;
    });
    currentStagePromises.set(projectId, promise);
  }
  return promise;
}

/** Test seam — drop all memoised flow data. */
export function __resetFlowCaches(): void {
  catalogPromise = null;
  currentStagePromises.clear();
}

export interface IssueFlowData {
  /** Global stage catalog (ordered), or empty until loaded / when no mirror rows. */
  catalog: ProjectStage[];
  /** Current stage per project id (null = project has no current stage set). */
  currentByProject: Map<string, ProjectStage | null>;
}

interface FlowIssueLike {
  raw?: { origin_kind?: string | null; origin_id?: string | null } | null;
}

/**
 * Collect the distinct projects behind the stage-mirror rows in `issues` and
 * batch-load the catalog + each project's current stage (deduped + cached).
 * Non-mirror issues contribute nothing, so a list with zero mirror rows fires
 * no requests.
 */
export function useIssueFlows(issues: FlowIssueLike[]): IssueFlowData {
  const [catalog, setCatalog] = useState<ProjectStage[]>([]);
  const [currentByProject, setCurrentByProject] = useState<
    Map<string, ProjectStage | null>
  >(() => new Map());

  const projectIds = useMemo(() => {
    const ids = new Set<string>();
    for (const issue of issues) {
      const ref = parseStageMirror(issue.raw?.origin_kind, issue.raw?.origin_id);
      if (ref) ids.add(ref.projectId);
    }
    return Array.from(ids).sort();
  }, [issues]);

  const projectKey = projectIds.join(',');

  useEffect(() => {
    if (projectIds.length === 0) {
      setCatalog([]);
      setCurrentByProject(new Map());
      return;
    }
    let cancelled = false;

    loadStageCatalog()
      .then((rows) => {
        if (!cancelled) setCatalog(rows);
      })
      .catch((err) => console.error('[issueFlow] catalog load failed', err));

    Promise.all(
      projectIds.map(async (projectId) => {
        const stage = await loadCurrentStage(projectId).catch((err) => {
          console.error('[issueFlow] current stage load failed', projectId, err);
          return null;
        });
        return [projectId, stage] as const;
      }),
    ).then((entries) => {
      if (!cancelled) setCurrentByProject(new Map(entries));
    });

    return () => {
      cancelled = true;
    };
    // projectKey is the stable digest of projectIds.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectKey]);

  return { catalog, currentByProject };
}
