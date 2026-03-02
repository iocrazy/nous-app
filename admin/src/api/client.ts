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
 * Get access token, with fallback to localStorage when Lock API hangs.
 */
async function getAccessToken(): Promise<string | null> {
  try {
    const result = await Promise.race([
      supabase.auth.getSession().then(({ data }) => data.session?.access_token ?? null),
      new Promise<null>((res) => setTimeout(() => res(null), 1000)),
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
        if (parsed?.access_token) return parsed.access_token
      } catch { /* ignore */ }
    }
  }
  return null
}

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
    const message =
      error.response?.data?.message ||
      error.response?.data?.detail ||
      'Request failed'

    // Auto-report API errors (avoid loop on error reporting endpoint itself)
    const url = error.config?.url || ''
    if (!url.includes('/errors/report')) {
      import('./endpoints/error-reporting').then(({ reportError }) => {
        reportError({
          error_type: 'network',
          message: `${error.config?.method?.toUpperCase() || 'UNKNOWN'} ${url} — ${message}`,
          url: window.location.href,
          metadata: {
            status: error.response?.status,
            api_url: url,
          },
        })
      }).catch(() => {})
    }

    return Promise.reject(new Error(message))
  },
)
