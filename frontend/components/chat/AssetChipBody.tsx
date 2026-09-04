import React from 'react';
import { FileQuestion, X } from 'lucide-react';

import { ASSET_TYPE_ICON } from '../resources/assets/assetTypeMeta';
import type { AssetType } from '../assets/assetSlots';
import { getResourceCoverUrl } from '../../services/resourceService';

/**
 * A staged library ASSET, in the composer's attachment row.
 *
 * Same agent tokens and sizing as `ResourceChipBody`'s `staged` variant, so a
 * character and a video sitting side by side read as one row rather than two
 * unrelated widgets. What it deliberately does NOT carry is the resource
 * chip's status dot: that dot is a live Task Center reading about
 * transcription / summarisation, and an asset has no such work — painting a
 * dot here would answer a question nobody asked about this row.
 *
 * The cover comes from `cover_file_id` through the ordinary resource cover
 * route (an asset's cover IS a resource). The fallback is the asset's TYPE
 * icon, not a generic file glyph: "this is a character" is the most useful
 * thing a coverless chip can still say.
 */

export interface AssetChipBodyProps {
  assetId: string;
  name: string;
  /** One of `ASSET_TYPES`; anything else falls back to a neutral glyph. */
  assetType: string;
  /** `assets.cover_file_id`, or '' when the asset has no cover. */
  coverFileId?: string;
  onRemove: () => void;
}

const CHIP_CLASS =
  'inline-flex items-center gap-1.5 px-2 py-0.5 rounded border border-agent-line '
  + 'bg-agent-soft text-agent text-[11px] select-none';

export function AssetChipBody({
  assetId,
  name,
  assetType,
  coverFileId = '',
  onRemove,
}: AssetChipBodyProps): React.ReactElement {
  // An unknown type is possible in one real case: a bubble or draft written
  // by an older build, before a seventh asset type existed. A neutral glyph
  // is honest there; indexing blindly would crash the whole composer row.
  const Icon = ASSET_TYPE_ICON[assetType as AssetType] ?? FileQuestion;

  return (
    <span data-testid="staged-asset-chip" data-asset-id={assetId} className={CHIP_CLASS}>
      {coverFileId ? (
        <img
          src={getResourceCoverUrl(coverFileId)}
          alt=""
          data-testid="staged-asset-chip-thumb"
          className="w-4 h-4 rounded object-cover"
        />
      ) : (
        <span data-testid="staged-asset-chip-icon" className="inline-flex shrink-0">
          <Icon size={11} />
        </span>
      )}
      <span className="font-medium truncate max-w-[120px]">{name}</span>
      <button
        type="button"
        onClick={onRemove}
        className="opacity-50 hover:opacity-100 ml-0.5 inline-flex"
        aria-label="remove"
      >
        <X size={10} />
      </button>
    </span>
  );
}

export default AssetChipBody;
