import axios from 'axios'
import { supabase } from '../auth/supabase'

// In dev, use relative URLs so requests go through Vite proxy (no CORS).
// In production, use VITE_API_URL for direct backend access.
const API_URL = import.meta.env.DEV ? '' : (import.meta.env.VITE_API_URL || '')

export const apiClient = axios.create({
  baseURL: API_URL,
  headers: { 'Content-Type': 'application/json' },
})

/**
 * Get access token with auto-refresh support.
 * 1. Try supabase.auth.getSession() (triggers refresh if expired)
 * 2. If Lock API blocks, fall back to localStorage
 * 3. If localStorage token looks expired, attempt explicit refresh
 */
async function getAccessToken(): Promise<string | null> {
  // Try the Supabase client first — it auto-refreshes expired tokens
  try {
    const result = await Promise.race([
      supabase.auth.getSession().then(({ data }) => data.session?.access_token ?? null),
      new Promise<null>((res) => setTimeout(() => res(null), 1500)),
    ])
    if (result) return result
  } catch {
    // fall through to localStorage
  }

  // Fallback: read token directly from localStorage
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key?.startsWith('sb-') && key.endsWith('-auth-token')) {
      try {
        const parsed = JSON.parse(localStorage.getItem(key) || '')
        if (parsed?.access_token) {
          // Check if token is expired by decoding JWT payload
          const payload = JSON.parse(atob(parsed.access_token.split('.')[1]))
          const isExpired = payload.exp * 1000 < Date.now()

          if (!isExpired) return parsed.access_token

          // Token expired — try explicit refresh using refresh_token
          if (parsed.refresh_token) {
            try {
              const { data } = await supabase.auth.refreshSession({
                refresh_token: parsed.refresh_token,
              })
              if (data.session?.access_token) return data.session.access_token
            } catch {
              // refresh failed, token is truly expired
            }
          }
        }
      } catch { /* ignore */ }
    }
  }
  return null
}

// Track 401 redirect to avoid infinite loops
let isRedirectingToLogin = false

apiClient.interceptors.request.use(async (config) => {
  const token = await getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status
    const message =
      error.response?.data?.message ||
      error.response?.data?.detail ||
      'Request failed'

    // Handle 401: session expired → redirect to login
    if (status === 401 && !isRedirectingToLogin) {
      isRedirectingToLogin = true
      // Clear stale session and redirect
      supabase.auth.signOut().catch(() => {})
      window.location.href = '/login'
      return new Promise(() => {}) // prevent further error handling
    }

    // Auto-report API errors (avoid loop on error reporting endpoint itself)
    const url = error.config?.url || ''
    if (!url.includes('/errors/report') && status !== 401) {
      import('./endpoints/error-reporting').then(({ reportError }) => {
        reportError({
          error_type: 'network',
          message: `${error.config?.method?.toUpperCase() || 'UNKNOWN'} ${url} — ${message}`,
          url: window.location.href,
          metadata: {
            status,
            api_url: url,
          },
        })
      }).catch(() => {})
    }

    return Promise.reject(new Error(message))
  },
)
