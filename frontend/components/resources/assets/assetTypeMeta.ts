// frontend/components/resources/assets/assetTypeMeta.ts
//
// Shelf-side presentation for the six asset types: the lucide icon a type
// wears, and which i18n key names it.
//
// The icon table is a SECOND copy of `ResourcesSidebar`'s `ASSET_TYPE_ICON`.
// That one is private to the sidebar and this task does not own that file, so
// the choice was "duplicate six lines" or "widen someone else's module mid
// task". Both tables are keyed by `AssetType`, so adding a seventh type fails
// to compile in both places — the drift this duplication could cause is a
// build error, not a blank icon.
//
// Lucide only — no emoji anywhere in the UI (CLAUDE.md).

import type { ComponentType } from 'react';
import { AudioLines, FileText, MapPin, Package, Shirt, Users } from 'lucide-react';

import type { AssetType } from '../../assets/assetSlots';

export type AssetIcon = ComponentType<{ size?: number; className?: string }>;

export const ASSET_TYPE_ICON: Record<AssetType, AssetIcon> = {
  character: Users,
  location: MapPin,
  prop: Package,
  costume: Shirt,
  prompt: FileText,
  audio: AudioLines,
};

/**
 * The PLURAL shelf label ("Characters"). Same namespace the sidebar rail and
 * the type tabs read, so a shelf and its rail entry can never disagree.
 */
export function typeLabelKey(type: AssetType): string {
  return `assets.types.${type}`;
}

/**
 * The SINGULAR label ("Character"), for anything naming ONE asset: the
 * "+ New" menu, the New dialog's title.
 *
 * Deliberately the `saveAsAsset.type.*` namespace rather than a second copy
 * under `assets.*`: that set already exists in both locales, was written for
 * exactly this meaning, and a duplicate would be one more thing to translate
 * twice and keep in step. The plural set (`assets.types.*`) names SHELVES and
 * is a different question — see `i18nParity.test.ts`.
 */
export function typeSingularKey(type: AssetType): string {
  return `saveAsAsset.type.${type}`;
}

/**
 * A slot's label key. Reuses `saveAsAsset.slot.*` for the same reason as
 * above; it already covers the union of every type's slots plus `unsorted`,
 * and a slot missing from it renders its raw value rather than a blank.
 */
export function slotLabelKey(slot: string): string {
  return `saveAsAsset.slot.${slot}`;
}
