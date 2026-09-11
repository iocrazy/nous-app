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
}

/** The picker's row, reduced to what a citation needs. */
export function toStagedOutput(row: OutputMentionRow): StagedOutputRef {
  return {
    ref_kind: row.ref_kind,
    ref_id: row.ref_id,
    version: row.version,
    title: row.title,
  };
}

const same = (a: StagedOutputRef, kind: string, refId: string, version: number): boolean =>
  a.ref_kind === kind && a.ref_id === refId && a.version === version;

/**
 * Append a citation.
 *
 * Three answers, and the caller must tell them apart:
 *   - a NEW list           → staged;
 *   - the SAME list back   → already staged, nothing to say;
 *   - `null`               → over the cap, and the caller must SAY so.
 *
 * Null rather than a silent truncation because the server refuses the whole
 * comment past `MAX_OUTPUT_REF_ATTACHMENTS` — a ninth chip that looked staged
 * would produce a comment the writer believes was sent and that never posts.
 * A duplicate at the cap still returns the list unchanged: nothing would be
 * added, so reporting a limit the writer has not exceeded would be a lie about
 * their own action.
 */
export function stageOutput(
  list: StagedOutputRef[],
  row: StagedOutputRef,
): StagedOutputRef[] | null {
  const next = {
    ref_kind: row.ref_kind,
    ref_id: String(row.ref_id ?? ''),
    version: row.version,
    title: row.title ?? null,
  };
  // A chip with no id could never resolve; staging it would promise the writer
  // a reference that comes back refused.
  if (!next.ref_kind || !next.ref_id || !Number.isFinite(next.version)) return list;
  if (list.some((s) => same(s, next.ref_kind, next.ref_id, next.version))) return list;
  if (list.length >= MAX_OUTPUT_REF_ATTACHMENTS) return null;
  return [...list, next];
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

/** The wire form — the five keys `output_ref_resolver._stamped` stores. */
export function toOutputAttachment(staged: StagedOutputRef): OutputRefAttachment {
  return {
    kind: 'output_ref',
    ref_kind: staged.ref_kind,
    ref_id: staged.ref_id,
    version: staged.version,
    title: staged.title,
  };
}
