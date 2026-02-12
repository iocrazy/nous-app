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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserIdentity | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const fetchProfile = useCallback(async (authUser: User): Promise<UserIdentity | null> => {
    const { data: profile } = await supabase
      .from('user_profiles')
      .select('username, avatar_url, role')
      .eq('id', authUser.id)
      .single()

    if (!profile) return null

    return {
      id: authUser.id,
      email: authUser.email || '',
      name: profile.username || authUser.email || '',
      avatar: profile.avatar_url || undefined,
      role: profile.role,
    }
  }, [])

  useEffect(() => {
    supabase.auth.getSession().then(async ({ data: { session: s } }) => {
      setSession(s)
      if (s?.user) {
        const identity = await fetchProfile(s.user)
        if (identity?.role === 'admin') {
          setUser(identity)
        } else {
          setUser(null)
        }
      }
      setIsLoading(false)
    })

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (_event, s) => {
        setSession(s)
        if (s?.user) {
          const identity = await fetchProfile(s.user)
          if (identity?.role === 'admin') {
            setUser(identity)
          } else {
            setUser(null)
          }
        } else {
          setUser(null)
        }
      },
    )

    return () => subscription.unsubscribe()
  }, [fetchProfile])

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
