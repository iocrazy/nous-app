/**
 * "Send to Agent" — stage a library resource as a chip in the floating chat,
 * topping up whatever AI processing it is missing on the way.
 *
 * Two right-click menus reach this: the resource library's
 * (`useContextMenuItems`) and My Downloads' (`DownloadContextMenu`). They are
 * separate components with separate item shapes, which is exactly why the
 * chain lives here instead of being written twice — the first copy already
 * cost the user the whole My Downloads view (1242 items, and the surface
 * they actually work in) for a release, because the feature was wired to
 * one menu and not the other.
 *
 * The processing top-up runs BEFORE staging but is not awaited before it: a
 * failed or slow trigger must never swallow the send. The user gets the asset
 * in the composer either way; a failure only means the agent reads less, and
 * it is surfaced as a toast rather than a silent no-op (CLAUDE.md's
 * "触发路径必须类型化失败回显").
 */

import { useGlobalChatStore } from '../stores/globalChatStore';
import { ensureResourceProcessed } from './ensureResourceProcessed';
import { resourceProcessingNotice } from './resourceProcessingToast';

export type AgentResourceKind = 'video' | 'image' | 'doc' | 'audio' | 'pdf';

/** The subset of a resource row this path reads. Deliberately loose about
 *  the source: the library passes a `Resource`, My Downloads assembles one
 *  from the `resources` row behind a `parsed_media` item. */
export interface AgentSendableResource {
  id: string;
  filename?: string | null;
  mime_type?: string | null;
  file_type?: string | null;
  media_id?: string | number | null;
  thumbnail_path?: string | null;
  cover_image_path?: string | null;
  transcript_status?: string | null;
  summary_status?: string | null;
}

/** Canonical kind for the chip, mirroring the backend's
 *  `app/services/ai/_mime_kind.py`, so a resource looks the same however it
 *  reached the composer. `file_type` is the fallback for rows whose mime
 *  never got recorded — note that downloads store aweme-type numerals there
 *  ('0', '68', …), which is why mime wins and unknown falls through to doc. */
export function resourceKind(
  resource: AgentSendableResource | undefined,
): AgentResourceKind {
  const mime = (resource?.mime_type ?? '').toLowerCase();
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime === 'application/pdf') return 'pdf';
  const fileType = (resource?.file_type ?? '').toLowerCase();
  if (fileType === 'video' || fileType === 'image' || fileType === 'audio' || fileType === 'pdf') {
    return fileType;
  }
  return 'doc';
}

/** Same ladder as the search router's `_thumbnail_url` and the picker's
 *  `buildThumbnailSrc`: bet on a cover whenever any signal exists. The bet
 *  can lose (the endpoint 404s for some parsed_media rows), which is why
 *  the chip renders the URL behind an onError icon fallback. */
export function resourceCoverPath(
  resource: AgentSendableResource | undefined,
): string | null {
  if (!resource?.id) return null;
  const isImage = resource.mime_type?.startsWith('image/') ?? false;
  if (resource.thumbnail_path || resource.cover_image_path || resource.media_id || isImage) {
    return `/api/v1/resources/${resource.id}/cover`;
  }
  return null;
}

/** i18next's `t(key, defaultValue, options)` shape, narrowed to what we use
 *  (same narrowing `resourceProcessingNotice` applies). */
type Translate = (
  key: string,
  defaultValue: string,
  options?: Record<string, unknown>,
) => string;

export interface SendResourceToAgentOptions {
  scope: { type: 'personal' | 'team'; id: string };
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
  t: Translate;
}

/**
 * Resolves once the processing top-up has settled — callers may ignore it,
 * but awaiting keeps a test (or a caller that wants to disable its button)
 * able to observe the whole chain.
 */
export async function sendResourceToAgent(
  resource: AgentSendableResource,
  { scope, addToast, t }: SendResourceToAgentOptions,
): Promise<void> {
  const id = String(resource.id);
  const kind = resourceKind(resource);

  // The status columns ride along on purpose (RECON#15): without them the
  // helper reads "never transcribed" and re-triggers a PAID transcription,
  // because the endpoint dedups in-flight work only — never finished work.
  const processing = ensureResourceProcessed({
    id,
    kind,
    mime: resource.mime_type,
    transcript_status: resource.transcript_status,
    summary_status: resource.summary_status,
  }).then((result) => {
    const notice = resourceProcessingNotice(result, t);
    if (notice) addToast(notice.message, notice.type);
  });

  // Stage regardless of how the top-up goes.
  useGlobalChatStore.getState().sendResourceToChat({
    resourceId: id,
    name: resource.filename ?? '',
    kind,
    mime: resource.mime_type ?? null,
    scope,
    thumbnailUrl: resourceCoverPath(resource),
    transcriptStatus: resource.transcript_status ?? null,
    summaryStatus: resource.summary_status ?? null,
  });

  await processing;
}
