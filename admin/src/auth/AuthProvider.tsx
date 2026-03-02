import { createContext, useContext, useEffect, useState, useCallback, type ReactNode } from 'react'
import type { User, Session } from '@supabase/supabase-js'
import { supabase } from './supabase'

interface UserIdentity {
  id: string
  email: string
  name: string
  avatar?: string
  role: string
}

interface AuthContextType {
  user: UserIdentity | null
  session: Session | null
  isAdmin: boolean
  isLoading: boolean
  login: (email: string, password: string) => Promise<{ error?: string }>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

/**
 * Try to recover a Supabase session from localStorage.
 * Supabase JS v2 stores sessions under a key like "sb-<ref>-auth-token".
 * When the browser Lock API hangs, getSession() never resolves,
 * so we read localStorage directly as a fallback.
 */
function recoverSessionFromStorage(): { user: User; access_token: string } | null {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && key.startsWith('sb-') && key.endsWith('-auth-token')) {
        const raw = localStorage.getItem(key)
        if (!raw) continue
        const parsed = JSON.parse(raw)
        if (parsed?.access_token && parsed?.user) {
          return { user: parsed.user, access_token: parsed.access_token }
        }
      }
    }
  } catch {
    // ignore parse errors
  }
  return null
}

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY

/**
 * Fetch user profile via raw fetch() instead of Supabase client.
 * When the Lock API blocks the Supabase client, all .from() queries
 * also hang. This bypasses the client entirely.
 */
async function fetchProfileRaw(
  userId: string,
  email: string,
  accessToken: string,
): Promise<UserIdentity | null> {
  try {
    const url = `${SUPABASE_URL}/rest/v1/user_profiles?select=username,avatar_url,role&id=eq.${userId}`
    const resp = await fetch(url, {
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${accessToken}`,
      },
    })
    if (!resp.ok) return null
    const rows = await resp.json()
    const profile = rows?.[0]
    if (!profile) return null
    return {
      id: userId,
      email,
      name: profile.username || email,
      avatar: profile.avatar_url || undefined,
      role: profile.role,
    }
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserIdentity | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const fetchProfile = useCallback(async (authUser: User): Promise<UserIdentity | null> => {
    try {
      const { data: profile, error } = await supabase
        .from('user_profiles')
        .select('username, avatar_url, role')
        .eq('id', authUser.id)
        .single()

      if (error || !profile) return null

      return {
        id: authUser.id,
        email: authUser.email || '',
        name: profile.username || authUser.email || '',
        avatar: profile.avatar_url || undefined,
        role: profile.role,
      }
    } catch {
      return null
    }
  }, [])

  const loadIdentity = useCallback(async (authUser: User) => {
    try {
      const identity = await fetchProfile(authUser)
      if (identity?.role === 'admin') {
        setUser(identity)
      } else {
        setUser(null)
      }
    } catch {
      setUser(null)
    }
  }, [fetchProfile])

  useEffect(() => {
    let cancelled = false

    async function initSession() {
      // Race getSession() against a 2s timeout.
      // If the Lock API hangs, fall back to reading localStorage directly.
      const TIMEOUT_MS = 2000
      let resolved = false

      const sessionResult = await Promise.race([
        supabase.auth.getSession().then(({ data }) => {
          resolved = true
          return data.session
        }).catch(() => null),
        new Promise<null>(res => setTimeout(() => {
          if (!resolved) res(null)
        }, TIMEOUT_MS)),
      ])

      if (cancelled) return

      if (sessionResult?.user) {
        setSession(sessionResult)
        await loadIdentity(sessionResult.user)
      } else {
        // Fallback: read session from localStorage and use raw fetch
        // (Supabase client data queries also hang when Lock API blocks)
        const stored = recoverSessionFromStorage()
        if (stored) {
          const identity = await fetchProfileRaw(
            stored.user.id,
            stored.user.email || '',
            stored.access_token,
          )
          if (identity?.role === 'admin') {
            setUser(identity)
          }
        }
      }

      if (!cancelled) setIsLoading(false)
    }

    initSession()

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (_event, s) => {
        setSession(s)
        if (s?.user) {
          await loadIdentity(s.user)
        } else {
          setUser(null)
        }
      },
    )

    return () => {
      cancelled = true
      subscription.unsubscribe()
    }
  }, [loadIdentity])

  const login = useCallback(async (email: string, password: string) => {
    const { data, error } = await supabase.auth.signInWithPassword({ email, password })

    if (error) {
      return { error: error.message }
    }

    const profile = await fetchProfile(data.user)
    if (profile?.role !== 'admin') {
      await supabase.auth.signOut()
      return { error: 'Access denied. Admin role required.' }
    }

    setUser(profile)
    return {}
  }, [fetchProfile])

  const logout = useCallback(async () => {
    await supabase.auth.signOut()
    setUser(null)
    setSession(null)
  }, [])

  return (
    <AuthContext.Provider
      value={{
        user,
        session,
        isAdmin: user?.role === 'admin',
        isLoading,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
