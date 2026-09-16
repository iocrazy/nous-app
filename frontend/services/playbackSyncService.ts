// frontend/services/playbackSyncService.ts
// Cross-device resume points — /api/v1/playback-positions (mig 473).
//
// This is the SECOND of two layers. `utils/playbackResume.ts` is the first: a
// per-user localStorage store that answers instantly, works offline, and is
// what makes a deploy-triggered reload resume with no network round trip. This
// one is what makes a phone and a laptop agree.
//
// Every call here is best-effort. A player whose resume breaks because the
// network hiccuped is worse than one that quietly falls back to the local
// answer, so nothing in this module throws at its callers.

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

export interface RemotePosition {
  media_key: string;
  position_seconds: number;
  duration_seconds: number;
  /** Server time, stored verbatim by the caller — see `playbackResume.sv`. */
  updated_at: string;
}

const base = () => `${getApiUrl()}/api/v1/playback-positions`;

/**
 * Fetch the stored position for one media key, or null.
 *
 * Returns null for "nothing stored" AND for "could not ask" on purpose: both
 * mean the same thing to the caller, which is "fall back to what you have
 * locally". The difference is logged, not surfaced.
 */
export async function fetchRemotePosition(
  mediaKey: string,
): Promise<RemotePosition | null> {
  if (!mediaKey) return null;
  try {
    const url = `${base()}?media_keys=${encodeURIComponent(mediaKey)}`;
    const res = await fetch(url, { headers: await getAuthHeaders() });
    if (!res.ok) {
      console.error('[playbackSync] fetch failed', res.status);
      return null;
    }
    const body = (await res.json()) as { positions?: RemotePosition[] };
    return body.positions?.find((p) => p.media_key === mediaKey) ?? null;
  } catch (err) {
    console.error('[playbackSync] fetch threw', err);
    return null;
  }
}

/**
 * Push a position. Returns the server's `updated_at` so the caller can record
 * "this is my own write", or null if the push did not land.
 *
 * `keepalive` lets the request outlive the page, which is the whole point of
 * the flush on `pagehide`: a normal fetch is cancelled when the document goes
 * away, and that is exactly the moment the last position matters most.
 * `sendBeacon` cannot be used instead — it has no way to set the
 * Authorization header.
 */
export async function pushRemotePosition(
  mediaKey: string,
  positionSeconds: number,
  durationSeconds: number,
  opts: { keepalive?: boolean } = {},
): Promise<string | null> {
  if (!mediaKey || !(durationSeconds > 0)) return null;
  try {
    const res = await fetch(base(), {
      method: 'PUT',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        media_key: mediaKey,
        // Clamp rather than let the server 422 it: a position a hair past the
        // duration is a rounding artefact of the media element, not a bug
        // worth failing the write over.
        position_seconds: Math.min(positionSeconds, durationSeconds),
        duration_seconds: durationSeconds,
      }),
      keepalive: opts.keepalive === true,
    });
    if (!res.ok) {
      console.error('[playbackSync] push failed', res.status);
      return null;
    }
    const body = (await res.json()) as RemotePosition;
    return body.updated_at ?? null;
  } catch (err) {
    console.error('[playbackSync] push threw', err);
    return null;
  }
}

/** Forget a position server-side (the video played out). Best-effort. */
export async function deleteRemotePosition(mediaKey: string): Promise<void> {
  if (!mediaKey) return;
  try {
    const url = `${base()}?media_key=${encodeURIComponent(mediaKey)}`;
    const res = await fetch(url, { method: 'DELETE', headers: await getAuthHeaders() });
    if (!res.ok) console.error('[playbackSync] delete failed', res.status);
  } catch (err) {
    console.error('[playbackSync] delete threw', err);
  }
}
