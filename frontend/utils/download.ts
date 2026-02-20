import { getSupabaseClient } from '../supabaseClient';

/**
 * Get auth headers from Supabase session for authenticated downloads.
 */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  const { data: sessionData } = await getSupabaseClient()?.auth.getSession() || {};
  const token = sessionData?.session?.access_token;
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

/**
 * Universal file download via fetch + blob.
 * Works for both same-origin and cross-origin URLs (when CORS allows).
 *
 * @param url       - The URL to download from
 * @param filename  - Suggested filename for the downloaded file
 * @param options   - Optional auth headers and callbacks
 */
export async function downloadFile(
  url: string,
  filename: string,
  options?: {
    headers?: Record<string, string>;
    onSuccess?: (filename: string) => void;
    onError?: (message: string) => void;
  },
): Promise<boolean> {
  try {
    const fetchOptions: RequestInit = options?.headers ? { headers: options.headers } : {};
    const response = await fetch(url, fetchOptions);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    // Respect Content-Disposition filename from server
    const contentDisposition = response.headers.get('Content-Disposition');
    if (contentDisposition) {
      const match = contentDisposition.match(/filename="(.+)"/);
      if (match) filename = match[1];
    }

    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(blobUrl);

    options?.onSuccess?.(filename);
    return true;
  } catch (error) {
    // CORS or network error on external URLs — fall back to opening in new tab
    if (error instanceof TypeError && url.startsWith('http')) {
      console.warn('Download blocked by CORS, opening in new tab:', url);
      window.open(url, '_blank');
      options?.onSuccess?.(filename);
      return true;
    }
    const msg = error instanceof Error ? error.message : 'Unknown error';
    console.error('Download error:', msg, url);
    options?.onError?.(msg);
    return false;
  }
}

/**
 * Download a file with Supabase auth headers (for backend API downloads).
 */
export async function downloadWithAuth(
  url: string,
  filename: string,
  options?: {
    onSuccess?: (filename: string) => void;
    onError?: (message: string) => void;
  },
): Promise<boolean> {
  const headers = await getAuthHeaders();
  return downloadFile(url, filename, { ...options, headers });
}
