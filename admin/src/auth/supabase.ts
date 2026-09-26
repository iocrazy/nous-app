import { createClient } from '@supabase/supabase-js'

import { resolveSupabaseUrl } from './supabaseUrl'

// Empty VITE_SUPABASE_URL = same-origin `/sb` (see supabaseUrl.ts).
const supabaseUrl = resolveSupabaseUrl(import.meta.env.VITE_SUPABASE_URL, window.location.origin)
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!supabaseAnonKey) {
  throw new Error('Missing Supabase environment variables')
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey)
