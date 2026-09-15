/**
 * Profile service — 主账号名（the account name) + the stable numeric ID.
 *
 * `username` lives on `public.user_profiles`, NOT in Supabase Auth's
 * `user_metadata`. That distinction is the whole reason this file exists: the
 * app used to "save" the name through `supabase.auth.updateUser({data:…})`,
 * which writes metadata and leaves the column every list in the product
 * actually renders — team members, ProjectCard initials, the points board —
 * untouched. The user saw "Saved", and their name did not change.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { decodeErrorEnvelope } from './errorEnvelope';

export interface Profile {
  /** 主账号名. Generated at signup, user-editable, globally unique (case-insensitive). */
  username: string;
  /** Stable numeric id (snowflake). Survives a rename — it is what identifies you. */
  display_id: string;
  avatar_url?: string | null;
}

/** Why a profile request failed, in a form the UI can branch on. */
export class ProfileError extends Error {
  /** `username_taken` | `username_invalid` | `profile_schema_pending` | … */
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = 'ProfileError';
    this.code = code;
  }
}

/**
 * Read the backend's typed error envelope through the one decoder
 * (`services/errorEnvelope.ts`).
 *
 * The route-specific reason we care about (`username_taken` vs
 * `username_invalid`) rides in `details.code`; the envelope's own `code` is
 * only ever `http_409`, which cannot tell the two apart — it is kept as the
 * SECOND choice here (the other services drop it), because a profile refusal
 * that typed nothing is still better named `http_409` than by a status the
 * caller re-derives.
 *
 * The copy chain is this service's own and deliberately unlike the canonical
 * one: the envelope's `error` comes before a plain-string `detail`, because
 * every profile route that refuses in prose does it through `error`.
 */
async function profileError(res: Response, fallback: string): Promise<ProfileError> {
  let code = `http_${res.status}`;
  let message = `${fallback} (HTTP ${res.status})`;
  try {
    const decoded = decodeErrorEnvelope(await res.json());
    code = decoded.code ?? decoded.envelopeCode ?? code;
    message = decoded.typedMessage ?? decoded.error ?? decoded.detailText ?? message;
  } catch {
    /* body was not JSON — keep the status-derived fallback */
  }
  return new ProfileError(code, message);
}

export async function fetchProfile(): Promise<Profile> {
  const res = await fetch(`${getApiUrl()}/api/v1/auth/profile`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw await profileError(res, 'Failed to load profile');
  return res.json();
}

/**
 * Change 主账号名.
 *
 * Throws `ProfileError` with `username_taken` when somebody already has it —
 * the backend deliberately does NOT quietly append a suffix here. Auto-suffixing
 * is right when the system hands out a DEFAULT name (the user expressed no
 * preference); doing it to a name the user typed, and answering "saved", is the
 * worst outcome — they find out later and believe they chose it.
 */
export async function updateUsername(username: string): Promise<Profile> {
  const res = await fetch(`${getApiUrl()}/api/v1/auth/profile`, {
    method: 'PATCH',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ username }),
  });
  if (!res.ok) throw await profileError(res, 'Failed to change the account name');
  return res.json();
}
