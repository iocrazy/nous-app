// features/canvas-core/smart/mentionedAssets.ts
//
// Assets named INSIDE a prompt body with `@`, as opposed to wired in as a card.
//
// Product ruling: an `@`-mentioned asset creates NO node. It is an inline chip
// in the text, and at run time it is delivered exactly like an upstream asset
// card — the same `GET /assets/{id}/bundle` call, the same primary-slot files
// as references, the same prompt prefix. The only differences are ordering
// (mentions come after wired cards) and where the outcome is reported (the
// prompt node itself, because there is no card to put a badge on).
//
// ─ Why a token and not just the name ────────────────────────────────────────
//
// The prompt body is persisted as PLAIN TEXT (`PromptNodeData.body`); the chips
// are a projection of it. If a mention rendered into the body as `@Hero`, the
// text could not say WHICH asset that was — two assets may share a name, and a
// rename would silently re-point the reference. So the body carries
// `@[asset:<snowflake>]`, which is stable, and the id is what the chip, the
// bundle call and the provenance stamp all read.
//
// The token is an implementation detail of storage and MUST NOT reach the
// model: `promptBodyForRun` replaces each one with the asset's NAME before the
// text is dispatched. A token in a generation prompt would be noise the model
// tries to interpret.
//
// ⚠️ Coexists with the older `@Image N` convention (`promptImageRefs.ts`),
// which is a DIFFERENT thing: that names one of this node's input pictures and
// projects into the text as the plain alias. The two are distinguishable
// because only this one is bracketed, and `@Image 1` cannot match the token
// pattern.

import type { AssetType } from '../../../components/assets/assetSlots';

/**
 * One asset named in a prompt body.
 *
 * Everything here is a SNAPSHOT taken when the mention was inserted, for the
 * same reason `PromptResourceRef` snapshots a name: the chip has to render
 * (and the run text has to read) without a round trip per keystroke. The
 * `asset_id` is the authority — if the asset was renamed, the run still
 * bundles the right asset and only the label is stale.
 */
export interface MentionedAsset {
  /** `assets.id` — Snowflake as a string (bigint-safe). */
  asset_id: string;
  /** Display name at insert time; what the model is shown in place of the token. */
  name: string;
  asset_type: AssetType;
  /** `resources.id` of the cover, for the chip and the input-strip thumbnail. */
  cover_file_id: string | null;
}

/** The literal a mention writes into the prompt text. */
export function assetMentionToken(assetId: string): string {
  return `@[asset:${assetId}]`;
}

/**
 * A fresh global matcher every call.
 *
 * A shared `/g` RegExp carries `lastIndex` between calls, so two callers using
 * the same instance skip matches depending on who ran first — a stateful bug
 * that only shows up once there are two consumers.
 */
export function assetMentionPattern(): RegExp {
  return /@\[asset:([0-9A-Za-z_-]+)\]/g;
}

/** One run of a prompt body: literal text, or a mention token. */
export type MentionSegment =
  | { kind: 'text'; text: string }
  | { kind: 'asset'; assetId: string };

/**
 * Split a body into text runs and mention tokens, in order.
 *
 * This is what rebuilds the chips after a reload: the document is thrown away
 * on every mount and the body text is all that survives, so the token has to
 * be able to re-become a chip at the position it occupied.
 */
export function splitMentionSegments(text: string): MentionSegment[] {
  const out: MentionSegment[] = [];
  const re = assetMentionPattern();
  let last = 0;
  for (let m = re.exec(text); m !== null; m = re.exec(text)) {
    if (m.index > last) out.push({ kind: 'text', text: text.slice(last, m.index) });
    out.push({ kind: 'asset', assetId: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}

/** The asset ids named in a body, in reading order, deduped. */
export function mentionedAssetIds(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const seg of splitMentionSegments(text)) {
    if (seg.kind !== 'asset' || seen.has(seg.assetId)) continue;
    seen.add(seg.assetId);
    out.push(seg.assetId);
  }
  return out;
}

/**
 * `mentioned_assets` narrowed to the entries whose token is still in the text,
 * in the order the text names them.
 *
 * Deleting the chip deletes the reference — the document is the list, exactly
 * as it is for image chips. This function is the same rule for anything that
 * only has the persisted text and the persisted list (a reload, a body
 * rewritten from outside the editor).
 */
export function pruneMentionedAssets(
  text: string,
  list: readonly MentionedAsset[],
): MentionedAsset[] {
  const byId = new Map(list.map((a) => [a.asset_id, a]));
  const out: MentionedAsset[] = [];
  for (const id of mentionedAssetIds(text)) {
    const found = byId.get(id);
    if (found) out.push(found);
  }
  return out;
}

/**
 * The body as the MODEL receives it: every token replaced by the asset's name.
 *
 * A token whose asset is not in `list` is removed rather than left in place.
 * That is the honest match to what the run actually does — an unknown id
 * contributes no bundle either, so leaving `@[asset:123]` in the prompt would
 * describe a reference that is not being sent. It happens only when a body was
 * pasted or edited outside the picker.
 */
export function renderMentionText(
  text: string,
  list: readonly MentionedAsset[],
): string {
  const byId = new Map(list.map((a) => [a.asset_id, a]));
  return splitMentionSegments(text)
    .map((seg) => {
      if (seg.kind === 'text') return seg.text;
      const found = byId.get(seg.assetId);
      return found ? `@${found.name}` : '';
    })
    .join('');
}

/**
 * The one function every run site must use to read a prompt's body.
 *
 * There are four of them (composer Run/Cascade, chain run, loop round,
 * rerun/retry) and each builds its own `RunnerContext`. Reading `data.body`
 * directly at any of them ships the raw token to the provider, which is
 * invisible in a diff and invisible in a passing test — so the read is named,
 * and `mentionedAssets.guard.test.ts` fails if a run site goes back to the
 * bare field.
 */
export function promptBodyForRun(data: {
  body?: string;
  mentioned_assets?: MentionedAsset[];
}): string {
  return renderMentionText(data.body ?? '', data.mentioned_assets ?? []);
}
