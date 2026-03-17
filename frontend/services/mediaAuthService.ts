// frontend/services/mediaAuthService.ts

/**
 * Media session cookie management.
 *
 * After JWT login, we call POST /api/v1/auth/media-session to set an httpOnly
 * cookie that authenticates /media/ requests (for <video>/<img> tags that
 * can't send Authorization headers).
 */

const getApiUrl = (): string => {
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    return import.meta.env.VITE_API_URL || '';
  }
  return '';
};

/**
 * Create a media session cookie by sending the JWT to the backend.
 * The backend validates the JWT, creates a signed cookie, and sets it.
 */
export async function createMediaSession(accessToken: string): Promise<void> {
  try {
    const apiUrl = getApiUrl();
    const response = await fetch(`${apiUrl}/api/v1/auth/media-session`, {
      method: 'POST',
      credentials: 'include', // Required to receive cross-origin cookies
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      console.error('Failed to create media session:', response.status);
    }
  } catch (err) {
    console.error('Failed to create media session:', err);
  }
}

/**
 * Clear the media session cookie on logout.
 */
export async function deleteMediaSession(): Promise<void> {
  try {
    const apiUrl = getApiUrl();
    const response = await fetch(`${apiUrl}/api/v1/auth/media-session`, {
      method: 'DELETE',
      credentials: 'include',
    });

    if (!response.ok) {
      console.error('Failed to delete media session:', response.status);
    }
  } catch (err) {
    console.error('Failed to delete media session:', err);
  }
}
