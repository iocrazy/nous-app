// frontend/components/resources/assets/AssetCard.tsx
//
// One asset on the shelf (spec screen 3). The card answers four questions a
// writer asks before opening anything: who is this (portrait + name + role),
// how complete is it (ring + slot squares), can I use it yet (Ready / Draft,
// and if not — WHAT is missing), and where is it already used (project chips).
//
// Two of those are easy to get subtly wrong, so they are spelled out here:
//
//  * The RING and the CHIP answer different questions and must not be
//    collapsed into one. The chip is readiness — a server-derived boolean
//    ("does the primary slot hold a file"). The ring is coverage — how many of
//    the type's slots have anything in them at all. A character with only a
//    sheet is Ready with a nearly empty ring, and that is the honest picture.
//  * `missing` is RENDERED, not counted. "Draft" on its own tells a user they
//    cannot use the asset without telling them what to do about it; the server
//    already names the slots, and dropping that on the floor is the
//    silent-no-op class this repo keeps re-learning.

import React from 'react';
import { useTranslation } from 'react-i18next';

import { PRIMARY_SLOT, SLOTS } from '../../assets/assetSlots';
import type { AssetRow } from '../../../services/assetsService';
import { getResourceCoverUrl } from '../../../services/resourceService';
import { ASSET_TYPE_ICON, slotLabelKey, typeSingularKey } from './assetTypeMeta';

/** How many project chips fit before the rest become "+N". */
const PROJECT_CHIP_LIMIT = 2;

/**
 * What a project chip shows when no name is known for the id.
 *
 * A Snowflake id is 15-19 digits. Rendering it raw was the bug this replaces:
 * the chip read as a wall of numbers, wide enough to crowd out the chip beside
 * it and meaningless to anyone. A short `#…1234` is honest about being an id
 * and about being abbreviated, and the caller pairs it with a `title` carrying
 * the full value so the information is still reachable.
 *
 * Ids short enough to be readable are shown whole — the `#` prefix alone
 * already says "this is an id, not a name". Exported for its test: the cutoff
 * is the part worth pinning.
 */
export function shortProjectLabel(projectId: string): string {
  return projectId.length > 6 ? `#…${projectId.slice(-4)}` : `#${projectId}`;
}

export interface AssetCardProps {
  asset: AssetRow;
  /** Project id → display name, for the chips.
   *
   *  An id with no entry still renders — a project the caller could not name
   *  is still a project this asset is used in, and dropping the chip would
   *  under-report where the asset is used. It falls back to
   *  {@link shortProjectLabel} with the full id in a `title`, never to the raw
   *  15-digit Snowflake as the visible label.
   *
   *  A caller that omits the map entirely gets the fallback for EVERY chip, so
   *  pass it wherever a project list is already on hand. */
  projectNames?: Record<string, string>;
  /** The All tab shows a type tag; a single-type shelf does not need one. */
  showTypeTag?: boolean;
  onOpen: (asset: AssetRow) => void;
}

/**
 * Coverage of the type's named slots, as a fraction in [0, 1].
 *
 * `prompt` is the one type whose primary is NOT a file slot (`PRIMARY_SLOT`
 * is null — its body is the primary), so for it the readiness state itself
 * counts as one unit. Without that a ready prompt preset would draw an empty
 * ring in the "ok" colour, which reads as a contradiction.
 *
 * Exported for its test: the arithmetic is the part worth pinning, not the
 * SVG geometry around it.
 */
export function slotCoverage(asset: AssetRow): { filled: number; total: number } {
  const named = SLOTS[asset.asset_type] ?? [];
  const bodyIsPrimary = PRIMARY_SLOT[asset.asset_type] === null;
  const filledSlots = named.filter((slot) => (asset.file_counts_by_slot?.[slot] ?? 0) > 0).length;
  return {
    filled: filledSlots + (bodyIsPrimary && asset.readiness.state === 'ready' ? 1 : 0),
    total: named.length + (bodyIsPrimary ? 1 : 0),
  };
}

const RING_RADIUS = 8;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

interface ReadinessRingProps {
  ready: boolean;
  filled: number;
  total: number;
  label: string;
}

/** A 22px progress ring. Semantic tokens only — `ok` when the asset is
 *  usable, `warn` when it still wants something (CLAUDE.md: no legacy hue
 *  class names for state). */
const ReadinessRing: React.FC<ReadinessRingProps> = ({ ready, filled, total, label }) => {
  const fraction = total > 0 ? Math.min(1, Math.max(0, filled / total)) : 0;
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 22 22"
      role="img"
      aria-label={label}
      data-testid="readiness-ring"
      data-ring-filled={filled}
      data-ring-total={total}
    >
      <title>{label}</title>
      <circle
        cx="11"
        cy="11"
        r={RING_RADIUS}
        fill="none"
        strokeWidth="2.5"
        className="stroke-line-strong"
      />
      <circle
        cx="11"
        cy="11"
        r={RING_RADIUS}
        fill="none"
        strokeWidth="2.5"
        strokeLinecap="round"
        // Dasharray, not a path: one arc, no trigonometry to get wrong.
        strokeDasharray={`${RING_CIRCUMFERENCE * fraction} ${RING_CIRCUMFERENCE}`}
        transform="rotate(-90 11 11)"
        className={ready ? 'stroke-ok' : 'stroke-warn'}
      />
    </svg>
  );
};

export const AssetCard: React.FC<AssetCardProps> = ({
  asset,
  projectNames,
  showTypeTag = false,
  onOpen,
}) => {
  const { t } = useTranslation();

  const Icon = ASSET_TYPE_ICON[asset.asset_type];
  const ready = asset.readiness.state === 'ready';
  const { filled, total } = slotCoverage(asset);
  const namedSlots = SLOTS[asset.asset_type] ?? [];

  const ringLabel = t('assets.card.coverage', {
    filled,
    total,
    defaultValue: '{{filled}} of {{total}} slots filled',
  });

  // `missing` is server-derived and already carries slot NAMES; translating
  // each through the shared slot namespace keeps a shelf chip reading the
  // same as the slot picker in the Save-as-Asset dialog.
  const missingLabel = asset.readiness.missing
    .map((slot) => t(slotLabelKey(slot), slot))
    .join(', ');

  const projectIds = asset.project_ids ?? [];
  const shownProjects = projectIds.slice(0, PROJECT_CHIP_LIMIT);
  const overflowProjects = projectIds.length - shownProjects.length;

  return (
    <button
      type="button"
      onClick={() => onOpen(asset)}
      data-testid="asset-card"
      data-asset-id={asset.id}
      data-asset-type={asset.asset_type}
      data-readiness={asset.readiness.state}
      aria-label={t('assets.card.open', {
        name: asset.name,
        defaultValue: 'Open {{name}}',
      })}
      className="group flex flex-col overflow-hidden rounded-xl border border-line bg-card text-left transition-colors hover:border-line-strong"
    >
      <div className="relative aspect-[4/5] w-full bg-island-2">
        {asset.cover_file_id ? (
          <img
            src={getResourceCoverUrl(asset.cover_file_id)}
            alt={asset.name}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-content-4">
            <Icon size={28} />
          </div>
        )}

        {/* Library membership (mig 448). Rendered ONLY when the asset is out
            — a badge on every in-library card would be noise on the shelf,
            where being in the library is the norm. On the project panel, where
            both states sit side by side, this is what tells the user which
            cards the Add To Library action still applies to. */}
        {asset.in_library === false && (
          <span
            data-testid="not-in-library-badge"
            title={t(
              'assets.library.notInLibraryHint',
              'Lives on this project only — add it to reuse it elsewhere',
            )}
            className="absolute bottom-1.5 left-1.5 rounded-full border border-line-strong bg-card/90 px-1.5 text-[10px] font-medium text-content-3"
          >
            {t('assets.library.notInLibrary', 'Not In Library')}
          </span>
        )}

        {showTypeTag && (
          <span
            data-testid="asset-card-type-tag"
            className="absolute left-1.5 top-1.5 rounded-full border border-line-strong bg-card/90 px-1.5 text-[10px] font-medium text-content-3"
          >
            {t(typeSingularKey(asset.asset_type), asset.asset_type)}
          </span>
        )}

        <span className="absolute right-1.5 top-1.5">
          <ReadinessRing ready={ready} filled={filled} total={total} label={ringLabel} />
        </span>
      </div>

      <div className="flex flex-col gap-1 px-2 pb-2 pt-1.5">
        <div className="truncate text-[12px] font-medium text-content" title={asset.name}>
          {asset.name}
        </div>
        {asset.role_tag !== '' && (
          <div className="truncate text-[11px] text-content-3" title={asset.role_tag}>
            {asset.role_tag}
          </div>
        )}

        {/* Slot squares, in slot-table order (primary first). Filled squares
            carry a count in their tooltip; an empty one is dashed rather than
            absent, so the shelf shows the SHAPE of a complete asset of this
            type and not just what happens to exist. */}
        {namedSlots.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-0.5" data-testid="asset-card-slots">
            {namedSlots.map((slot) => {
              const count = asset.file_counts_by_slot?.[slot] ?? 0;
              const slotName = t(slotLabelKey(slot), slot);
              return (
                <span
                  key={slot}
                  data-slot={slot}
                  data-slot-filled={count > 0}
                  title={`${slotName} · ${count}`}
                  className={`h-2.5 w-2.5 rounded-[3px] border ${
                    count > 0
                      ? 'border-accent bg-accent-soft'
                      : 'border-dashed border-line-strong bg-transparent'
                  }`}
                />
              );
            })}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-1 pt-0.5">
          <span
            data-testid="readiness-chip"
            className={`rounded-full border px-1.5 text-[10px] font-medium ${
              ready
                ? 'border-ok-line bg-ok-soft text-ok'
                : 'border-warn-line bg-warn-soft text-warn'
            }`}
          >
            {ready
              ? t('assets.readiness.ready', 'Ready')
              : missingLabel === ''
                ? t('assets.readiness.draft', 'Draft')
                : `${t('assets.readiness.draft', 'Draft')} · ${t('assets.card.missing', {
                    slots: missingLabel,
                    defaultValue: 'Missing: {{slots}}',
                  })}`}
          </span>

          {shownProjects.map((projectId) => {
            const name = projectNames?.[projectId];
            return (
              <span
                key={projectId}
                data-testid="asset-card-project"
                data-project-id={projectId}
                data-project-named={name !== undefined}
                // The full id in the tooltip either way: when the name is
                // known the chip is truncated, and when it is not the label is
                // abbreviated. Both hide something the user may need.
                title={name ?? projectId}
                className="max-w-[7rem] truncate rounded-full border border-line-strong px-1.5 text-[10px] text-content-3"
              >
                {name ?? shortProjectLabel(projectId)}
              </span>
            );
          })}
          {overflowProjects > 0 && (
            <span className="text-[10px] tabular-nums text-content-4">
              {t('assets.card.moreProjects', {
                n: overflowProjects,
                defaultValue: '+{{n}}',
              })}
            </span>
          )}
        </div>
      </div>
    </button>
  );
};
