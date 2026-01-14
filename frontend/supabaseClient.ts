
import { createClient, SupabaseClient } from '@supabase/supabase-js';

// Safe environment variable access
const getEnv = (key: string): string | undefined => {
  try {
    // Check for Vite-style env vars
    // @ts-ignore
    if (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env[key]) {
      // @ts-ignore
      return import.meta.env[key];
    }
  } catch (e) {}

  try {
    // Check for process.env (legacy/Node)
    if (typeof process !== 'undefined' && process.env) {
      return process.env[key];
    }
  } catch (e) {
    console.warn(`Error accessing process.env.${key}`, e);
  }
  return undefined;
};

// Access LocalStorage safely
const getLocal = (key: string): string | null => {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage.getItem(key);
    }
  } catch (e) {}
  return null;
};

// Priority: LocalStorage (User Config) > Environment Variables (.env)
const supabaseUrl = getLocal('douyin_supabase_url') || getEnv('VITE_SUPABASE_URL') || getEnv('REACT_APP_SUPABASE_URL');
const supabaseAnonKey = getLocal('douyin_supabase_key') || getEnv('VITE_SUPABASE_ANON_KEY') || getEnv('REACT_APP_SUPABASE_ANON_KEY');

export const isSupabaseConfigured = (): boolean => {
  // Check if variables are set
  const isSet = (
    typeof supabaseUrl === 'string' &&
    supabaseUrl.trim().length > 0 &&
    typeof supabaseAnonKey === 'string' &&
    supabaseAnonKey.trim().length > 0
  );

  if (!isSet) {
    console.warn('Supabase is not configured. Please set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in .env file.');
    return false;
  }

  // Basic validation: URL should be a valid Supabase URL
  if (!supabaseUrl?.includes('supabase.co')) {
    console.warn('Invalid Supabase URL format');
    return false;
  }

  return true;
};

// Initialize client with error handling
let client: SupabaseClient | null = null;

if (isSupabaseConfigured()) {
  try {
    client = createClient(supabaseUrl!, supabaseAnonKey!);
    console.log(`Supabase Client initialized with URL: ${supabaseUrl}`);
  } catch (error) {
    console.error("Failed to initialize Supabase client:", error);
    client = null;
  }
}

export const supabase = client;

// Helper to get Supabase session token for API calls
export const getSupabaseAccessToken = async (): Promise<string | null> => {
  if (!supabase) return null;
  try {
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token || null;
  } catch (e) {
    console.error('Failed to get Supabase session:', e);
    return null;
  }
};
