// frontend/components/resources/mediaKindPlaceholder.ts
//
// The `media_kind` values that have no picture to show, and what to draw
// instead.
//
// `generated_media.media_kind` carries four values today. Two of them frame
// something visual (`image`, `video`); the other two do not:
//
//   audio  a My Uploads audio file saved through "As Asset" — minted by
//          `generated_inbox_service.py` (`ACCEPTED_ASSET_FILE_KINDS`)
//   file   a chat upload that is neither image nor video — `chat_upload.py`'s
//          `media_kind_for_mime` folds every other mime here
//
// Both used to reach an `<img src=…/cover>`, which is a broken-image icon and
// nothing else. This module is the single place that says "this kind has no
// preview" so the grid card and the lightbox cannot disagree about it — they
// draw the placeholder differently (a 150px tile vs a full-screen viewer),
// but never about WHICH rows get one.
//
// Anything outside the four stays on the image path deliberately: the backend
// itself defaults an unrecognised mime to `image` (`media_kind_from_mime`), so
// treating an unknown kind as a file would contradict the writer.

import { AudioLines, File as FileIcon, type LucideIcon } from 'lucide-react';

export const NON_VISUAL_MEDIA_KINDS = ['audio', 'file'] as const;
export type NonVisualMediaKind = (typeof NON_VISUAL_MEDIA_KINDS)[number];

interface Placeholder {
  Icon: LucideIcon;
  /** i18n key for the kind's own word, when a caller wants to name it. */
  labelKey: string;
  fallback: string;
}

export const NON_VISUAL_MEDIA: Record<NonVisualMediaKind, Placeholder> = {
  audio: { Icon: AudioLines, labelKey: 'generated.type.audio', fallback: 'Audio' },
  file: { Icon: FileIcon, labelKey: 'generated.type.file', fallback: 'File' },
};

/** The placeholder for a `media_kind`, or `null` when the kind is visual. */
export function placeholderFor(kind: string | null | undefined): Placeholder | null {
  return kind === 'audio' || kind === 'file' ? NON_VISUAL_MEDIA[kind] : null;
}

/**
 * A short format badge from the row's mime — `audio/mpeg` → `MPEG`.
 *
 * The wire carries NO filename and NO duration for a generation
 * (`GeneratedItem` is the whole contract), so the mime subtype is the only
 * thing that distinguishes one silent tile from the next. Returns `null` when
 * there is nothing to show rather than an empty badge.
 */
export function mediaFormatLabel(mime: string | null | undefined): string | null {
  const subtype = (mime ?? '').split(';')[0].split('/')[1]?.trim() ?? '';
  if (!subtype) return null;
  // `audio/x-m4a` → `M4A`: the `x-` prefix is a registry detail, not a format.
  const cleaned = subtype.replace(/^x-/i, '').toUpperCase();
  return cleaned.length > 12 ? cleaned.slice(0, 12) : cleaned;
}
