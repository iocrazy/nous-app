import { AuthProvider } from '@refinedev/core'
import { supabase } from '../lib/supabase'

export const authProvider: AuthProvider = {
  login: async ({ email, password }) => {
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password,
    })

    if (error) {
      return {
        success: false,
        error: {
          name: 'LoginError',
          message: error.message,
        },
      }
    }

    // Check if user is admin
    const { data: profile } = await supabase
      .from('user_profiles')
      .select('role')
      .eq('id', data.user.id)
      .single()

    if (profile?.role !== 'admin') {
      await supabase.auth.signOut()
      return {
        success: false,
        error: {
          name: 'AuthorizationError',
          message: 'Access denied. Admin role required.',
        },
      }
    }

    return {
      success: true,
      redirectTo: '/',
    }
  },

  logout: async () => {
    const { error } = await supabase.auth.signOut()

    if (error) {
      return {
        success: false,
        error: {
          name: 'LogoutError',
          message: error.message,
        },
      }
    }

    return {
      success: true,
      redirectTo: '/login',
    }
  },

  check: async () => {
    const { data: { session } } = await supabase.auth.getSession()

    if (!session) {
      return {
        authenticated: false,
        redirectTo: '/login',
      }
    }

    // Verify admin role
    const { data: profile } = await supabase
      .from('user_profiles')
      .select('role')
      .eq('id', session.user.id)
      .single()

    if (profile?.role !== 'admin') {
      return {
        authenticated: false,
        redirectTo: '/login',
        error: {
          name: 'AuthorizationError',
          message: 'Admin role required',
        },
      }
    }

    return {
      authenticated: true,
    }
  },

  getPermissions: async () => {
    const { data: { session } } = await supabase.auth.getSession()

    if (!session) return null

    const { data: profile } = await supabase
      .from('user_profiles')
      .select('role')
      .eq('id', session.user.id)
      .single()

    return profile?.role
  },

  getIdentity: async () => {
    const { data: { user } } = await supabase.auth.getUser()

    if (!user) return null

    const { data: profile } = await supabase
      .from('user_profiles')
      .select('*')
      .eq('id', user.id)
      .single()

    return {
      id: user.id,
      email: user.email,
      name: profile?.username || user.email,
      avatar: profile?.avatar_url,
      role: profile?.role,
    }
  },

  onError: async (error) => {
    if (error.status === 401 || error.status === 403) {
      return {
        logout: true,
        redirectTo: '/login',
      }
    }

    return { error }
  },
}
