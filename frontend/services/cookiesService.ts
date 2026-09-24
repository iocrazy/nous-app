import { apiClient } from './apiClient';
import type {
  CookieListResponse,
  CookieStatus,
  PlatformHeaders,
} from '../types/api';

export type { CookieStatus, PlatformHeaders };

// GET /api/v1/settings/cookies
export const fetchCookieStatuses = async (): Promise<CookieStatus[]> => {
  const data = await apiClient.get<CookieListResponse>(
    '/api/v1/settings/cookies',
  );
  return data.cookies ?? [];
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

// GET /api/v1/settings/headers/{platform}
export const fetchHeaders = async (platform: string): Promise<PlatformHeaders> =>
  apiClient.get<PlatformHeaders>(`/api/v1/settings/headers/${platform}`);

// PUT /api/v1/settings/headers/{platform}
export const setHeaders = async (
  platform: string,
  headersText: string,
): Promise<void> => {
  await apiClient.put(`/api/v1/settings/headers/${platform}`, {
    headers_text: headersText,
  });
};
