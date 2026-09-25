/**
 * Shot index (PR 3): one video's cut list, indexing it, backfilling many, and
 * the on-demand frame URL. Shapes come from `types/api` (generated from the
 * backend contract); refusals arrive as the production ErrorResponse envelope
 * with the typed code in `details.code` — read it with `shotErrorCode`.
 */
import { apiClient, ApiError } from './apiClient';
import { getApiUrl } from '../utils/apiConfig';
import type { BackfillShotsResponse, IndexShotsResponse, ShotsResponse } from '../types/api';

/** `GET /resources/{id}/shots`. `indexed=false` + `[]` for a video nobody
 *  has indexed — not an error. */
export const getResourceShots = (resourceId: string): Promise<ShotsResponse> =>
  apiClient.get<ShotsResponse>(`/api/v1/resources/${resourceId}/shots`);

/** `POST /ai/analyze/index-shots/{id}` → 202 with the Task Center task id.
 *  Typed refusals: 409 `already_indexed` (pass `force` to re-cut) /
 *  `embedder_unconfigured` / `provider_no_image`, 422 `not_a_video` /
 *  `no_video_file`, 503 `vector_store_missing`. */
export const indexShots = (
  resourceId: string,
  opts: { force?: boolean } = {},
): Promise<IndexShotsResponse> =>
  apiClient.post<IndexShotsResponse>(`/api/v1/ai/analyze/index-shots/${resourceId}`, {
    force: opts.force ?? false,
  });

/** Same ceiling as the backend (`BackfillShotsBody.limit` le=50): every
 *  candidate becomes its own task, not one embedding call. */
export const SHOTS_BACKFILL_BATCH = 20;

/** `POST /ai/analyze/backfill-shots`. `dry_run` creates nothing and reports
 *  the candidates with a shot / token estimate. */
export const backfillShots = (body: {
  limit: number;
  dry_run: boolean;
}): Promise<BackfillShotsResponse> =>
  apiClient.post<BackfillShotsResponse>('/api/v1/ai/analyze/backfill-shots', body);

/** `GET /resources/{id}/frame?ms=` for a bare `<img src>`: the signed media
 *  token (`useAuth().mediaToken`) rides in `?token=` since an image element
 *  cannot send a header. */
export function getResourceFrameUrl(resourceId: string, ms: number, token?: string | null): string {
  const params = new URLSearchParams({ ms: String(Math.max(0, Math.round(ms))) });
  if (token) params.set('token', token);
  return `${getApiUrl()}/api/v1/resources/${resourceId}/frame?${params.toString()}`;
}

/** Typed refusal code of a shot-index call: `details.code` of the production
 *  ErrorResponse envelope, never the generic `http_<status>`. */
export function shotErrorCode(err: unknown): string | undefined {
  if (!(err instanceof ApiError)) return undefined;
  const details = err.details;
  const code = details && typeof details === 'object' ? (details as { code?: unknown }).code : undefined;
  return typeof code === 'string' ? code : undefined;
}
