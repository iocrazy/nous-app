/**
 * memoService — REST for beat timeline memos (Beats M5).
 *
 * A memo is a laper-style note that lives IN a script's Beats arrangement (its
 * own `beat_memos` table), never in the inspiration library. All ids are strings
 * — the backend serializes bigints via coerce_numbers_to_str, and callers must
 * String()-ify before sending so a Snowflake never round-trips as a JS number.
 *
 * Endpoints ({ success, data } envelope via unwrapResponse):
 *   GET    /scripts/{scriptId}/memos
 *   POST   /scripts/{scriptId}/memos
 *   POST   /scripts/{scriptId}/memos/upload   (multipart → { path })
 *   PATCH  /memos/{memoId}
 *   DELETE /memos/{memoId}
 *   GET    /scripts/{scriptId}/memos/{memoId}/images/{idx}?token=  (image bytes)
 */
import { getApiUrl } from '../../utils/apiConfig';
import { getAuthHeaders } from '../../services/parserService';
import { unwrapResponse } from '../../utils/apiHelpers';

const apiBase = () => `${getApiUrl()}/api/v1`;

export interface Memo {
  id: string;
  script_id: string;
  /** Whole-second offset on the script's beats timeline. */
  anchor_sec: number;
  content: string;
  /** Object-store path strings (≤4); served via memoImageUrl. */
  images: string[];
  created_at: string;
  updated_at: string;
}

export interface MemoInput {
  anchor_sec?: number;
  content?: string;
  images?: string[];
}

export async function listMemos(scriptId: string): Promise<Memo[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/memos`, { headers });
  return unwrapResponse<Memo[]>(res);
}

export async function createMemo(
  scriptId: string,
  data: { anchor_sec: number; content?: string; images?: string[] },
): Promise<Memo> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/memos`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Memo>(res);
}

export async function updateMemo(memoId: string, data: MemoInput): Promise<Memo> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${apiBase()}/memos/${memoId}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<Memo>(res);
}

export async function deleteMemo(memoId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${apiBase()}/memos/${memoId}`, { method: 'DELETE', headers });
}

/** Upload one memo image; returns its object-store path (stored in memo.images). */
export async function uploadMemoImage(scriptId: string, file: File): Promise<string> {
  const headers = { ...(await getAuthHeaders()) } as Record<string, string>;
  delete headers['Content-Type']; // browser sets the multipart boundary
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${apiBase()}/scripts/${scriptId}/memos/upload`, {
    method: 'POST',
    headers,
    body: form,
  });
  const { path } = await unwrapResponse<{ path: string }>(res);
  return path;
}

/**
 * Build the memo image URL for a browser-native `<img src>` load. The read is
 * bounded server-side to this memo + index; `?token=` carries the short-lived,
 * independently-revocable media token (AuthContext.mediaToken) because <img>
 * cannot send an Authorization header. Never pass the session JWT here.
 */
export function memoImageUrl(
  scriptId: string,
  memoId: string,
  idx: number,
  token?: string,
): string {
  const url = `${apiBase()}/scripts/${scriptId}/memos/${memoId}/images/${idx}`;
  return token ? `${url}?token=${encodeURIComponent(token)}` : url;
}
