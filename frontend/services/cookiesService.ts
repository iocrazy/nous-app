import { apiClient } from './apiClient';

export interface CookieStatus {
  platform: string;
  has_cookie: boolean;
  is_valid: boolean;
  error_message: string | null;
  updated_at: string | null;
}

// GET /api/v1/settings/cookies
export const fetchCookieStatuses = async (): Promise<CookieStatus[]> => {
  const data = await apiClient.get<{ cookies?: CookieStatus[] }>(
    '/api/v1/settings/cookies',
  );
  return data.cookies || [];
};

// PUT /api/v1/settings/cookies/{platform}
export const setCookie = async (
  platform: string,
  cookieText?: string,
  cookieFile?: string,
): Promise<void> => {
  await apiClient.put(`/api/v1/settings/cookies/${platform}`, {
    cookie_text: cookieText || null,
    cookie_file: cookieFile || null,
  });
};

// DELETE /api/v1/settings/cookies/{platform}
export const deleteCookie = async (platform: string): Promise<void> => {
  await apiClient.delete(`/api/v1/settings/cookies/${platform}`);
};

// ─── Custom Headers ─────────────────────────────────────

export interface HeadersData {
  platform: string;
  headers_text: string;
}

// GET /api/v1/settings/headers/{platform}
export const fetchHeaders = async (platform: string): Promise<HeadersData> =>
  apiClient.get<HeadersData>(`/api/v1/settings/headers/${platform}`);

// PUT /api/v1/settings/headers/{platform}
export const setHeaders = async (
  platform: string,
  headersText: string,
): Promise<void> => {
  await apiClient.put(`/api/v1/settings/headers/${platform}`, {
    headers_text: headersText,
  });
};
