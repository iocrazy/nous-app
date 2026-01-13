import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { supabase } from '@/lib/supabase'

type User = {
  id: string
  email: string
  user_metadata?: Record<string, unknown>
}

type Session = {
  access_token: string
  refresh_token: string
  expires_at?: number
}

type AuthState = {
  user: User | null
  session: Session | null
  isAuthenticated: boolean
  isLoading: boolean
  signIn: (email: string, password: string) => Promise<{ success: boolean; error?: string }>
  signUp: (email: string, password: string, username?: string) => Promise<{ success: boolean; error?: string }>
  signOut: () => Promise<void>
  refreshSession: () => Promise<void>
  setSession: (session: Session | null, user: User | null) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      session: null,
      isAuthenticated: false,
      isLoading: false,

      signIn: async (email: string, password: string) => {
        set({ isLoading: true })
        try {
          const { data, error } = await supabase.auth.signInWithPassword({
            email,
            password,
          })

          if (error) {
            set({ isLoading: false })
            return { success: false, error: error.message }
          }

          if (data.session && data.user) {
            set({
              user: {
                id: data.user.id,
                email: data.user.email!,
                user_metadata: data.user.user_metadata,
              },
              session: {
                access_token: data.session.access_token,
                refresh_token: data.session.refresh_token,
                expires_at: data.session.expires_at,
              },
              isAuthenticated: true,
              isLoading: false,
            })
            return { success: true }
          }

          set({ isLoading: false })
          return { success: false, error: '登录失败' }
        } catch (err) {
          set({ isLoading: false })
          return { success: false, error: String(err) }
        }
      },

      signUp: async (email: string, password: string, username?: string) => {
        set({ isLoading: true })
        try {
          const { data, error } = await supabase.auth.signUp({
            email,
            password,
            options: {
              data: username ? { username } : undefined,
            },
          })

          if (error) {
            set({ isLoading: false })
            return { success: false, error: error.message }
          }

          if (data.session && data.user) {
            set({
              user: {
                id: data.user.id,
                email: data.user.email!,
                user_metadata: data.user.user_metadata,
              },
              session: {
                access_token: data.session.access_token,
                refresh_token: data.session.refresh_token,
                expires_at: data.session.expires_at,
              },
              isAuthenticated: true,
              isLoading: false,
            })
            return { success: true }
          }

          set({ isLoading: false })
          return { success: true } // Email confirmation may be required
        } catch (err) {
          set({ isLoading: false })
          return { success: false, error: String(err) }
        }
      },

      signOut: async () => {
        await supabase.auth.signOut()
        set({
          user: null,
          session: null,
          isAuthenticated: false,
        })
      },

      refreshSession: async () => {
        const { session } = get()
        if (!session?.refresh_token) return

        try {
          const { data, error } = await supabase.auth.refreshSession({
            refresh_token: session.refresh_token,
          })

          if (error || !data.session) {
            get().signOut()
            return
          }

          set({
            session: {
              access_token: data.session.access_token,
              refresh_token: data.session.refresh_token,
              expires_at: data.session.expires_at,
            },
          })
        } catch {
          get().signOut()
        }
      },

      setSession: (session, user) => {
        set({
          session,
          user,
          isAuthenticated: !!session && !!user,
        })
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        user: state.user,
        session: state.session,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
)
