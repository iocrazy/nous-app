/**
 * Invite Service - Backend API proxy for team invite operations
 *
 * All invite operations go through the backend API instead of direct Supabase calls.
 */

import { getAuthHeaders } from './parserService';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

export interface TeamInvite {
  id: string;
  team_id: string;
  code: string;
  created_by: string;
  expires_at: string | null;
  max_uses: number | null;
  use_count: number;
  created_at: string;
}

export type ExpiryOption = '30m' | '1h' | '6h' | '12h' | '1d' | '7d' | 'never';

/**
 * Create a new invite for a team
 */
export const createInvite = async (
  teamId: string,
  options: {
    expiresIn?: ExpiryOption;
    maxUses?: number | null;
  } = {}
): Promise<TeamInvite> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/invites`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({
      team_id: teamId,
      expires_in: options.expiresIn || null,
      max_uses: options.maxUses || null,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Fetch all invites for a team
 */
export const fetchInvites = async (teamId: string): Promise<TeamInvite[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/invites?team_id=${teamId}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.invites || [];
};

/**
 * Delete an invite
 */
export const deleteInvite = async (inviteId: string): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/invites/${inviteId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });

  if (!response.ok && response.status !== 204) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Accept an invite and join the team
 */
export const acceptInvite = async (code: string): Promise<{ teamId: string; teamName: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/invites/accept`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ code }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    if (response.status === 404) {
      throw new Error('Invalid invite code');
    }
    if (response.status === 410) {
      throw new Error(error.detail || 'Invite has expired');
    }
    if (response.status === 409) {
      throw new Error('Already a member');
    }
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return {
    teamId: data.team_id,
    teamName: data.team_name,
  };
};

/**
 * Generate invite link from code
 */
export const getInviteLink = (code: string): string => {
  const baseUrl = window.location.origin;
  return `${baseUrl}/invite/${code}`;
};
