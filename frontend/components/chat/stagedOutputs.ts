/**
 * Staged output CITATIONS (harness 3a Task 6).
 *
 * A parallel, deliberately separate staging list beside the files, the
 * resources and the assets — the same reasoning `stagedResources.ts` gives for
 * keeping assets apart, one step further out: a citation carries no bytes, no
 * mime and no snapshot to paint from beyond a title, and the server resolves it
 * through `output_ref_resolver`, which validates that the cited version was
 * produced ON THIS ISSUE. Nothing else in the composer answers to that rule.
 *
 * The identity of a staged citation is `kind + id + VERSION`. That is not a
 * detail: citing v1 next to v3 of the same shot is how a reader says "this
 * changed", so the two must be able to coexist, and re-picking the exact same
 * version must not become a second chip.
 */

import type { OutputRefAttachment } from '../../types';
import { MAX_OUTPUT_REF_ATTACHMENTS } from './attachmentLimits';
import type { OutputMentionRow } from './outputMentionRows';

/**
 * One citation waiting above the composer.
 *
 * Every field crosses the boundary — unlike the other staged kinds there is no
 * composer-only snapshot half, because a citation IS its coordinates. `title`
 * is the exception in spirit only: the server overwrites it with the registry
 * row's title at post time, and it travels so the chip has words immediately.
 */
export interface StagedOutputRef {
  ref_kind: string;
  ref_id: string;
  version: number;
  title: string | null;
  /** The issue this version was produced on (3c §2.4) — a FACT about the
   *  version, carried whatever it is, NOT a flag for "came from elsewhere".
   *  The `@` search is scoped to the project, so this issue's own outputs are
   *  in the results with their own key; whether to SAY it is decided where the
   *  chip is drawn, against the issue the composer belongs to.
   *
   *  Staged rather than dropped because the chip is the SAME citation the
   *  picker showed and the thread will show: losing the source here makes one
   *  reference change identity twice across three adjacent screens, and a
   *  reader would reasonably read that as "the one I staged is a local one
   *  now". */
  issue_key: string | null;
}

/** The picker's row, reduced to what a citation needs. */
export function toStagedOutput(row: OutputMentionRow): StagedOutputRef {
  return {
    ref_kind: row.ref_kind,
    ref_id: row.ref_id,
    version: row.version,
    title: row.title,
    issue_key: row.issue_key,
  };
}

const same = (a: StagedOutputRef, kind: string, refId: string, version: number): boolean =>
  a.ref_kind === kind && a.ref_id === refId && a.version === version;

/**
 * What one pick did. FOUR outcomes, because the caller acts differently on
 * each — and two of them used to share an answer (B6).
 *
 *   - `staged`    → the row is now in `list`;
 *   - `duplicate` → it was already there; nothing to say, nothing to add;
 *   - `limit`     → over the cap, and the caller must SAY so;
 *   - `unusable`  → the row carries no usable coordinates, so nothing was
 *                   staged and the caller must SAY so.
 *
 * `unusable` used to return the list unchanged — the same answer a duplicate
 * gives — which made the pick a silent no-op: the picker closed, no chip
 * appeared, and the writer was told nothing («触发路径必须类型化失败回显»).
 */
export type StageOutputOutcome = 'staged' | 'duplicate' | 'limit' | 'unusable';

export interface StageOutputResult {
  outcome: StageOutputOutcome;
  /** The list to hold from here — the same array back unless `staged`, so the
   *  caller can set state unconditionally and React bails on identity. */
  list: StagedOutputRef[];
}

/**
 * Append a citation.
 *
 * Refusing past `MAX_OUTPUT_REF_ATTACHMENTS` rather than silently truncating,
 * because the server refuses the whole comment past that cap — a ninth chip
 * that looked staged would produce a comment the writer believes was sent and
 * that never posts. A duplicate at the cap is still `duplicate`: nothing would
 * be added, so reporting a limit the writer has not exceeded would be a lie
 * about their own action.
 */
export function stageOutput(
  list: StagedOutputRef[],
  row: StagedOutputRef,
): StageOutputResult {
  const next = {
    ref_kind: row.ref_kind,
    ref_id: String(row.ref_id ?? ''),
    version: row.version,
    title: row.title ?? null,
    issue_key: row.issue_key ?? null,
  };
  // A chip with no id could never resolve; staging it would promise the writer
  // a reference that comes back refused.
  if (!next.ref_kind || !next.ref_id || !Number.isFinite(next.version)) {
    return { outcome: 'unusable', list };
  }
  if (list.some((s) => same(s, next.ref_kind, next.ref_id, next.version))) {
    return { outcome: 'duplicate', list };
  }
  if (list.length >= MAX_OUTPUT_REF_ATTACHMENTS) return { outcome: 'limit', list };
  return { outcome: 'staged', list: [...list, next] };
}

/** Drop one staged citation (the chip's × button). Version-scoped: removing
 *  v3 must leave a separately staged v1 of the same object alone. */
export function removeStagedOutput(
  list: StagedOutputRef[],
  refKind: string,
  refId: string,
  version: number,
): StagedOutputRef[] {
  return list.filter((s) => !same(s, refKind, refId, version));
}

/** The wire form — the five keys `output_ref_resolver._stamped` stores.
 *
 * The one place `StagedOutputRef.ref_kind` (a string, because every value in
 * it came FROM `GET /issues/{id}/outputs`) meets `OutputRefAttachment.ref_kind`
 * (a `DeliverableKind`, so a client-assembled citation cannot be misspelt into
 * a post-time refusal). The cast states that boundary rather than hiding it:
 * an unknown kind still travels, and the server still answers
 * `output_ref_unresolvable` — narrowing the staged type instead would push a
 * cast up to the endpoint and lose nothing but the honesty about where the
 * unchecked value enters.
 */
export function toOutputAttachment(staged: StagedOutputRef): OutputRefAttachment {
  return {
    kind: 'output_ref',
    ref_kind: staged.ref_kind as OutputRefAttachment['ref_kind'],
    ref_id: staged.ref_id,
    version: staged.version,
    title: staged.title,
    // Overwritten server-side from the registry, exactly like `title`. It
    // travels so the optimistic row in the thread is not missing a chip the
    // refetched one has.
    issue_key: staged.issue_key,
  };
}
