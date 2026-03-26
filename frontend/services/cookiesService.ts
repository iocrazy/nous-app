import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

export interface CookieStatus {
  platform: string;
  has_cookie: boolean;
  is_valid: boolean;
  error_message: string | null;
  updated_at: string | null;
}

// GET /api/v1/settings/cookies
export const fetchCookieStatuses = async (): Promise<CookieStatus[]> => {
  const response = await fetch(`${getApiUrl()}/api/v1/settings/cookies`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.cookies || [];
};

// PUT /api/v1/settings/cookies/{platform}
export const setCookie = async (
  platform: string,
  cookieText?: string,
  cookieFile?: string,
): Promise<void> => {
  const response = await fetch(`${getApiUrl()}/api/v1/settings/cookies/${platform}`, {
    method: 'PUT',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ cookie_text: cookieText || null, cookie_file: cookieFile || null }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

// DELETE /api/v1/settings/cookies/{platform}
export const deleteCookie = async (platform: string): Promise<void> => {
  const response = await fetch(`${getApiUrl()}/api/v1/settings/cookies/${platform}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

// ─── Custom Headers ─────────────────────────────────────

export interface HeadersData {
  platform: string;
  headers_text: string;
}

// GET /api/v1/settings/headers/{platform}
export const fetchHeaders = async (platform: string): Promise<HeadersData> => {
  const response = await fetch(`${getApiUrl()}/api/v1/settings/headers/${platform}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  return response.json();
};

// PUT /api/v1/settings/headers/{platform}
export const setHeaders = async (platform: string, headersText: string): Promise<void> => {
  const response = await fetch(`${getApiUrl()}/api/v1/settings/headers/${platform}`, {
    method: 'PUT',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ headers_text: headersText }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};
