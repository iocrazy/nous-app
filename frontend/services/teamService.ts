/**
 * Team Service - Backend API proxy for team operations
 *
 * All team operations go through the backend API instead of direct Supabase calls.
 */

import { getAuthHeaders } from './parserService';
import { Team, TeamMember } from '../types';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

/**
 * Fetch all teams the current user is a member of
 */
export const fetchMyTeams = async (): Promise<Team[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/teams`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.teams || [];
};

/**
 * Create a new team
 */
export const createTeam = async (name: string): Promise<Team> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/teams`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ name }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Join a team by invite code
 */
export const joinTeamByCode = async (inviteCode: string): Promise<Team> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/teams/join`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ invite_code: inviteCode }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    if (response.status === 409) {
      throw new Error('Already a member');
    }
    if (response.status === 404) {
      throw new Error('Invalid invite code');
    }
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Leave a team (remove self from team)
 */
export const leaveTeam = async (teamId: string): Promise<void> => {
  const apiUrl = getApiUrl();

  // Get current user ID from session
  const authHeaders = getAuthHeaders();
  const meResponse = await fetch(`${apiUrl}/api/v1/auth/me`, {
    method: 'GET',
    headers: authHeaders,
  });

  if (!meResponse.ok) {
    throw new Error('Not authenticated');
  }

  const meData = await meResponse.json();
  const userId = meData.user?.id;

  if (!userId) {
    throw new Error('Not authenticated');
  }

  const response = await fetch(`${apiUrl}/api/v1/teams/${teamId}/members/${userId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });

  if (!response.ok && response.status !== 204) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Delete a team (owner only)
 */
export const deleteTeam = async (teamId: string): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/teams/${teamId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });

  if (!response.ok && response.status !== 204) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Fetch all members of a team
 */
export const fetchTeamMembers = async (teamId: string): Promise<TeamMember[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/teams/${teamId}/members`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.members || [];
};

/**
 * Update a team
 */
export const updateTeam = async (
  teamId: string,
  updates: { name?: string; description?: string }
): Promise<Team> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/teams/${teamId}`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify(updates),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Update a team member's role
 */
export const updateMemberRole = async (
  teamId: string,
  userId: string,
  role: 'admin' | 'member'
): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/teams/${teamId}/members/${userId}`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ role }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Remove a member from a team
 */
export const removeMember = async (teamId: string, userId: string): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/teams/${teamId}/members/${userId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });

  if (!response.ok && response.status !== 204) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};
