/**
 * Auth Service - Backend API proxy for authentication
 *
 * All auth operations go through the backend API instead of direct Supabase calls.
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

export interface AuthUser {
  id: string;
  email: string;
  username?: string;
}

export interface AuthResponse {
  success: boolean;
  message?: string;
  user?: AuthUser;
  session?: {
    access_token: string;
    refresh_token: string;
    expires_in: number;
  };
}

/**
 * Sign in with email and password
 */
export const signIn = async (email: string, password: string): Promise<AuthResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/auth/signin`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ email, password }),
  });

  const data = await response.json();

  if (!response.ok) {
    return {
      success: false,
      message: data.detail || data.message || 'Login failed',
    };
  }

  // Store session in localStorage for subsequent API calls
  if (data.session) {
    const storageKey = `sb-mediahub-auth-token`;
    localStorage.setItem(storageKey, JSON.stringify({
      access_token: data.session.access_token,
      refresh_token: data.session.refresh_token,
      expires_at: Date.now() + (data.session.expires_in * 1000),
    }));
  }

  return {
    success: true,
    user: data.user,
    session: data.session,
  };
};

/**
 * Sign up with email and password
 */
export const signUp = async (
  email: string,
  password: string,
  username?: string
): Promise<AuthResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/auth/signup`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ email, password, username }),
  });

  const data = await response.json();

  if (!response.ok) {
    return {
      success: false,
      message: data.detail || data.message || 'Registration failed',
    };
  }

  return {
    success: true,
    user: data.user,
    message: data.message,
  };
};

/**
 * Sign out
 */
export const signOut = async (): Promise<AuthResponse> => {
  const apiUrl = getApiUrl();

  try {
    await fetch(`${apiUrl}/api/v1/auth/signout`, {
      method: 'POST',
      headers: getAuthHeaders(),
    });
  } catch (e) {
    // Ignore errors on signout
  }

  // Clear local storage
  const keys = Object.keys(localStorage).filter(k =>
    k.startsWith('sb-') && k.endsWith('-auth-token')
  );
  keys.forEach(k => localStorage.removeItem(k));

  return { success: true };
};

/**
 * Get current user
 */
export const getCurrentUser = async (): Promise<AuthResponse> => {
  const apiUrl = getApiUrl();
  const headers = getAuthHeaders();

  // Check if we have an auth token
  if (!headers['Authorization']) {
    return { success: false, message: 'Not authenticated' };
  }

  const response = await fetch(`${apiUrl}/api/v1/auth/me`, {
    method: 'GET',
    headers,
  });

  const data = await response.json();

  if (!response.ok) {
    return {
      success: false,
      message: data.detail || 'Failed to get user',
    };
  }

  return {
    success: true,
    user: data.user,
  };
};

/**
 * Refresh session
 */
export const refreshSession = async (refreshToken: string): Promise<AuthResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/auth/refresh`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  const data = await response.json();

  if (!response.ok) {
    return {
      success: false,
      message: data.detail || 'Failed to refresh session',
    };
  }

  // Update stored session
  if (data.session) {
    const storageKey = `sb-mediahub-auth-token`;
    localStorage.setItem(storageKey, JSON.stringify({
      access_token: data.session.access_token,
      refresh_token: data.session.refresh_token,
      expires_at: Date.now() + (data.session.expires_in * 1000),
    }));
  }

  return {
    success: true,
    user: data.user,
    session: data.session,
  };
};

// Export as a service object for easy importing
export const authService = {
  signIn,
  signUp,
  signOut,
  getCurrentUser,
  refreshSession,
};
