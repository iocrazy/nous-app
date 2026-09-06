/**
 * Staged library resources — the composer's holding area for assets picked
 * from the @ menu or sent over from the library context menu.
 *
 * They used to be tiptap nodes sitting inside the sentence. They now live
 * beside the uploaded-file attachments, above the input, which means the
 * composer has two places a resource can come from on send: this array, and
 * whatever inline chips a draft (or a restored session) still carries. The
 * wire shape is unchanged either way — `ResourceRefAttachment` stays the one
 * producer, so the backend cannot tell which path an asset took.
 */

import type { AssetRefAttachment, ResourceRefAttachment } from '../../types';
import type { ResourceRefInsertItem } from './ChatInputResourceMention';

/**
 * A resource waiting above the composer. Carries both halves at once: the
 * wire fields (`resource_id` … `scope`) and the snapshot the chip paints
 * from until the Task Center has something fresher to say.
 */
export interface StagedResourceRef {
  resource_id: string;
  name: string;
  kind: string;
  mime: string;
  scope: { type: 'personal' | 'team'; id: string };
  /** Relative cover path (`/api/v1/resources/{id}/cover`) or ''. */
  thumbnail_url: string;
  transcript_status: string;
  summary_status: string;
}

/**
 * Normalise either entry point's item into the staged shape.
 *
 * Absent is normalised to '' rather than left undefined for the same reason
 * the tiptap node defaults its attrs to '': the status helpers treat an
 * empty status as "claims nothing", which is the right silence for the
 * context-menu path that never had those columns.
 */
export function toStagedResource(item: ResourceRefInsertItem): StagedResourceRef {
  return {
    resource_id: String(item.id ?? ''),
    name: item.name ?? '',
    kind: item.kind ?? 'doc',
    mime: item.mime ?? '',
    scope: item.scope ?? { type: 'personal', id: '' },
    thumbnail_url: item.thumbnail_url ?? '',
    transcript_status: item.transcript_status ?? '',
    summary_status: item.summary_status ?? '',
  };
}

/**
 * Append unless the same resource is already waiting. Staging twice is a
 * user double-click, not a request for two copies — and the payload dedups
 * anyway, so a second chip could only ever misreport what will be sent.
 */
export function stageResource(
  list: StagedResourceRef[],
  item: ResourceRefInsertItem,
): StagedResourceRef[] {
  const next = toStagedResource(item);
  if (!next.resource_id) return list;
  if (list.some((s) => s.resource_id === next.resource_id)) return list;
  return [...list, next];
}

/** Drop one staged resource by id (the chip's × button). */
export function removeStagedResource(
  list: StagedResourceRef[],
  resourceId: string,
): StagedResourceRef[] {
  return list.filter((s) => s.resource_id !== resourceId);
}

/** The wire form. Only these five fields cross the boundary. */
export function toRefAttachment(staged: StagedResourceRef): ResourceRefAttachment {
  return {
    kind: 'resource_ref',
    resource_id: staged.resource_id,
    name: staged.name,
    mime: staged.mime,
    scope: staged.scope,
  };
}

/**
 * Union of the two sources, deduped by resource id.
 *
 * Inline chips win a tie only in the sense that they keep their position —
 * the fields are the same snapshot either way. Sending one resource twice
 * would make the backend resolve and bill it twice, so the dedup is not
 * cosmetic.
 */
export function mergeRefAttachments(
  inline: ResourceRefAttachment[],
  staged: StagedResourceRef[],
): ResourceRefAttachment[] {
  const out: ResourceRefAttachment[] = [];
  const seen = new Set<string>();
  for (const ref of [...inline, ...staged.map(toRefAttachment)]) {
    if (seen.has(ref.resource_id)) continue;
    seen.add(ref.resource_id);
    out.push(ref);
  }
  return out;
}

// ─── Library assets (P5) ────────────────────────────────────────────────────
//
// A parallel, deliberately SEPARATE staging list. An asset is a library
// entity — a character, a location, a prompt — not a media file: it has no
// mime, no processing status, and the backend resolves it through a different
// resolver into a consistency prompt plus a primary image. Folding the two
// into one array would mean every consumer re-deriving which kind it holds,
// and the one that got it wrong would send an asset down the resource path,
// where it resolves to nothing and the user is told their attachment could
// not be READ.

/**
 * An asset waiting above the composer. Same two halves as
 * {@link StagedResourceRef}: the wire fields (`asset_id`, `loadout_id`,
 * `name`) and the snapshot the chip paints from (`asset_type`,
 * `cover_file_id`, `scope_id`), which die at the boundary.
 */
export interface StagedAssetRef {
  asset_id: string;
  /** Null means "the asset's default loadout" — the backend's reading, not a
   *  missing value. v2's `StagedAssetLoadoutMenu` is what sets it to anything
   *  else; both entry points still STAGE with null. */
  loadout_id: string | null;
  /**
   * The picked loadout's display name, or null.
   *
   * Composer-side only — it never crosses the boundary (`toAssetAttachment`
   * sends the id, and the server re-reads the name from the row it owns). It
   * exists so the chip can SAY which outfit is going with the message: a pick
   * whose only evidence is a field on the wire is a pick the user cannot see
   * they made, and therefore cannot correct.
   *
   * Always moves WITH `loadout_id` — null id means null name.
   */
  loadout_name: string | null;
  name: string;
  /** One of `ASSET_TYPES`; drives the chip's fallback icon. */
  asset_type: string;
  /** `assets.cover_file_id` → `/api/v1/resources/{id}/cover`, or '' when the
   *  asset has no cover and the chip should draw its type icon instead. */
  cover_file_id: string;
  /** Owning team id, or '' for a system preset. Snapshot only. */
  scope_id: string;
}

/** What either entry point hands the composer (the store channel's
 *  `PendingAsset`, or a row from the Assets tab of the @ picker). */
export interface AssetRefInsertItem {
  id: string;
  name?: string | null;
  asset_type?: string | null;
  loadout_id?: string | null;
  loadout_name?: string | null;
  cover_file_id?: string | null;
  scope_id?: string | null;
}

/** Normalise into the staged shape. Absent → '' for the snapshot fields for
 *  the same reason the resource path does it: an empty snapshot claims
 *  nothing, where `undefined` would have to be re-checked at every read.
 *  `loadout_id` is the exception — null there is a MEANING (the default
 *  loadout), so it is preserved rather than flattened. */
export function toStagedAsset(item: AssetRefInsertItem): StagedAssetRef {
  return {
    asset_id: String(item.id ?? ''),
    loadout_id: item.loadout_id ?? null,
    loadout_name: item.loadout_name ?? null,
    name: item.name ?? '',
    asset_type: item.asset_type ?? '',
    cover_file_id: item.cover_file_id ?? '',
    scope_id: item.scope_id ?? '',
  };
}

/** Append unless the same asset is already waiting (a double-click is one
 *  intent, not two copies). */
export function stageAsset(
  list: StagedAssetRef[],
  item: AssetRefInsertItem,
): StagedAssetRef[] {
  const next = toStagedAsset(item);
  if (!next.asset_id) return list;
  if (list.some((s) => s.asset_id === next.asset_id)) return list;
  return [...list, next];
}

/**
 * Re-dress one staged asset (the chip's loadout menu).
 *
 * Both fields move together — an id with a stale name would put the wrong
 * outfit on the chip while the right one goes on the wire, which is the exact
 * disagreement the label exists to prevent. Untouched assets are returned by
 * identity, so a menu on one chip cannot re-render the rest of the row.
 */
export function setStagedAssetLoadout(
  list: StagedAssetRef[],
  assetId: string,
  loadoutId: string | null,
  loadoutName: string | null,
): StagedAssetRef[] {
  return list.map((s) =>
    s.asset_id === assetId
      ? { ...s, loadout_id: loadoutId, loadout_name: loadoutName }
      : s,
  );
}

/** Drop one staged asset by id (the chip's × button). */
export function removeStagedAsset(
  list: StagedAssetRef[],
  assetId: string,
): StagedAssetRef[] {
  return list.filter((s) => s.asset_id !== assetId);
}

/**
 * The wire form. Only these fields cross the boundary — `asset_type`,
 * `cover_file_id`, `scope_id` and `loadout_name` are composer-side snapshots
 * the server does not read (it re-resolves the asset by id and re-checks
 * access by team membership), so sending them would state as fact something
 * the receiver would ignore. `loadout_id` alone is the pick; the NAME beside
 * it is for the chip.
 */
export function toAssetAttachment(staged: StagedAssetRef): AssetRefAttachment {
  return {
    kind: 'asset_ref',
    asset_id: staged.asset_id,
    loadout_id: staged.loadout_id,
    name: staged.name,
    mime: '',
    url: '',
  };
}

/**
 * The staged assets as attachments, deduped by `asset_id`.
 *
 * `stageAsset` already refuses a duplicate, so this is the second guard, not
 * the first — and it is the one that matters: sending one asset twice makes
 * the backend resolve it twice, render two `<asset>` entries for the same
 * row, and bill the turn for both.
 *
 * Deliberately NOT merged into `mergeRefAttachments`: assets have exactly one
 * source (this list — there is no inline tiptap asset node), and a shared
 * function would have to dedupe on two different id fields at once.
 */
export function mergeAssetAttachments(
  staged: StagedAssetRef[],
): AssetRefAttachment[] {
  const out: AssetRefAttachment[] = [];
  const seen = new Set<string>();
  for (const ref of staged.map(toAssetAttachment)) {
    if (seen.has(ref.asset_id)) continue;
    seen.add(ref.asset_id);
    out.push(ref);
  }
  return out;
}
