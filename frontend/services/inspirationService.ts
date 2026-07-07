// API layer for /api/v1/inspiration/* (P1 backend, PR #1137).
// All ids are strings — the backend serializes bigints via coerce_numbers_to_str.
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface NoteAttachment {
  id: string;
  mime: string;
  size_bytes: number;
  original_name: string;
}

export interface RefHotspot {
  hotspot_id?: string;
  title: string;
  source?: string;
  heat?: number;
  url?: string;
  captured_at?: string;
}

export interface InspirationNote {
  id: string;
  content_md: string;
  tags: string[];
  ref_hotspot: RefHotspot | null;
  pinned: boolean;
  note_date: string;
  created_at: string;
  updated_at: string;
  attachments: NoteAttachment[];
}

export interface NoteFilters {
  date?: string;
  tag?: string;
  q?: string;
}

const base = () => `${getApiUrl()}/api/v1/inspiration`;

async function jsonOrThrow(resp: Response) {
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(e.detail || e.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

export async function listNotes(
  filters: NoteFilters,
  limit = 50,
  beforeId?: string,
): Promise<InspirationNote[]> {
  const params = new URLSearchParams();
  if (filters.date) params.set('date', filters.date);
  if (filters.tag) params.set('tag', filters.tag);
  if (filters.q) params.set('q', filters.q);
  params.set('limit', String(limit));
  if (beforeId) params.set('before_id', beforeId);
  const resp = await fetch(`${base()}/notes?${params.toString()}`, {
    headers: await getAuthHeaders(),
  });
  return jsonOrThrow(resp);
}

export async function createNote(
  contentMd: string,
  refHotspot?: RefHotspot,
): Promise<InspirationNote> {
  const body: Record<string, unknown> = { content_md: contentMd };
  if (refHotspot) body.ref_hotspot = refHotspot;
  const resp = await fetch(`${base()}/notes`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return jsonOrThrow(resp);
}

export async function updateNote(
  id: string,
  patch: { content_md?: string; pinned?: boolean },
): Promise<InspirationNote> {
  const resp = await fetch(`${base()}/notes/${id}`, {
    method: 'PATCH',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return jsonOrThrow(resp);
}

export async function deleteNote(id: string): Promise<void> {
  const resp = await fetch(`${base()}/notes/${id}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Delete failed' }));
    throw new Error(e.detail || `HTTP ${resp.status}`);
  }
}

export async function getActivity(
  dateFrom: string,
  dateTo: string,
): Promise<{ day: string; cnt: number }[]> {
  const resp = await fetch(
    `${base()}/notes/activity?date_from=${dateFrom}&date_to=${dateTo}`,
    { headers: await getAuthHeaders() },
  );
  return jsonOrThrow(resp);
}

export async function getTagCounts(): Promise<{ tag: string; cnt: number }[]> {
  const resp = await fetch(`${base()}/notes/tags`, {
    headers: await getAuthHeaders(),
  });
  return jsonOrThrow(resp);
}

export async function uploadAttachment(
  noteId: string,
  file: File,
): Promise<NoteAttachment> {
  const headers = { ...(await getAuthHeaders()) } as Record<string, string>;
  delete headers['Content-Type']; // browser sets the multipart boundary
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${base()}/attachments/upload?note_id=${noteId}`, {
    method: 'POST',
    headers,
    body: form,
  });
  return jsonOrThrow(resp);
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  const resp = await fetch(`${base()}/attachments/${attachmentId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
}

export function attachmentUrl(attachmentId: string): string {
  return `${base()}/attachments/${attachmentId}`;
}
