/**
 * Per-scope chat temp resource TTL settings + the Promote action.
 *
 * The "Save" / "Save to folder..." actions in the resource library reuse the
 * existing POST /api/v1/resources/{id}/move endpoint (which writes folder_id
 * on the correct resource_items row, scoped by scope_type/scope_id). TTL
 * get/set hit the new /library/temp-ttl endpoints added in sub-plan 2.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

const apiBase = (): string => `${getApiUrl()}/api/v1`;

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json() as Promise<T>;
}

export type ScopeType = 'personal' | 'team';

export interface TempTtlResponse {
  ttl_days: number; // positive int = days; -1 = never expire
}

export interface PromoteDestination {
  folderId: string | null;
  scopeType: ScopeType;
  scopeId: string;
}

export const tempTtlService = {
  async getChatTempTtl(
    scope_type: ScopeType,
    scope_id: string,
  ): Promise<TempTtlResponse> {
    const qs = new URLSearchParams({ scope_type, scope_id });
    const resp = await fetch(
      `${apiBase()}/library/temp-ttl?${qs.toString()}`,
      { headers: await getAuthHeaders() },
    );
    return handle<TempTtlResponse>(resp);
  },

  async setChatTempTtl(
    scope_type: ScopeType,
    scope_id: string,
    ttl_days: number,
  ): Promise<TempTtlResponse> {
    const resp = await fetch(`${apiBase()}/library/temp-ttl`, {
      method: 'PUT',
      headers: {
        ...(await getAuthHeaders()),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ scope_type, scope_id, ttl_days }),
    });
    return handle<TempTtlResponse>(resp);
  },

  /**
   * Promote a temp resource — move it out of the `temp` folder.
   *
   * Wraps the existing `POST /api/v1/resources/{id}/move` endpoint. Scope
   * context is required because a resource may belong to multiple scopes
   * (the `resource_items` join carries scope membership). The caller passes
   * the scope they want to move WITHIN — typically the currently viewed scope.
   *
   * @param resourceId target resource id
   * @param dest       folder + scope context. `folderId=null` clears to scope root.
   */
  async promoteResource(
    resourceId: string,
    dest: PromoteDestination,
  ): Promise<{ success: boolean; data: unknown }> {
    const resp = await fetch(
      `${apiBase()}/resources/${encodeURIComponent(resourceId)}/move`,
      {
        method: 'POST',
        headers: {
          ...(await getAuthHeaders()),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          folder_id: dest.folderId,
          scope_type: dest.scopeType,
          scope_id: dest.scopeId,
        }),
      },
    );
    return handle<{ success: boolean; data: unknown }>(resp);
  },
};
