
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

// 配置存储（优先级: 后端YAML > LocalStorage > .env）
let configuredSupabaseUrl: string | null = null;
let configuredSupabaseAnonKey: string | null = null;

// 获取当前 Supabase URL（优先级: 后端配置 > .env）
const getSupabaseUrl = (): string | undefined => {
  if (configuredSupabaseUrl) return configuredSupabaseUrl;
  return getEnv('VITE_SUPABASE_URL') || getEnv('REACT_APP_SUPABASE_URL');
};

// 获取当前 Supabase Anon Key（优先级: 后端配置 > .env）
const getSupabaseAnonKey = (): string | undefined => {
  if (configuredSupabaseAnonKey) return configuredSupabaseAnonKey;
  return getEnv('VITE_SUPABASE_ANON_KEY') || getEnv('REACT_APP_SUPABASE_ANON_KEY');
};

export const isSupabaseConfigured = (): boolean => {
  const supabaseUrl = getSupabaseUrl();
  const supabaseAnonKey = getSupabaseAnonKey();

  // Check if variables are set
  const isSet = (
    typeof supabaseUrl === 'string' &&
    supabaseUrl.trim().length > 0 &&
    typeof supabaseAnonKey === 'string' &&
    supabaseAnonKey.trim().length > 0
  );

  if (!isSet) {
    console.warn('Supabase is not configured. Please set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in .env file or configure via backend.');
    return false;
  }

  // Basic validation: URL should be a valid URL (support self-hosted Supabase)
  try {
    new URL(supabaseUrl!);
  } catch {
    console.warn('Invalid Supabase URL format');
    return false;
  }

  return true;
};

// Initialize client with error handling
let client: SupabaseClient | null = null;

// 初始化 Supabase 客户端
const initializeClient = (): SupabaseClient | null => {
  const supabaseUrl = getSupabaseUrl();
  const supabaseAnonKey = getSupabaseAnonKey();

  if (!supabaseUrl || !supabaseAnonKey) {
    return null;
  }

  try {
    const newClient = createClient(supabaseUrl, supabaseAnonKey, {
      auth: {
        persistSession: true,        // Enable session persistence in localStorage
        autoRefreshToken: true,      // Automatically refresh tokens before expiry
        detectSessionInUrl: true,    // Handle OAuth redirects
      },
    });
    console.log(`Supabase Client initialized with URL: ${supabaseUrl}`);
    return newClient;
  } catch (error) {
    console.error("Failed to initialize Supabase client:", error);
    return null;
  }
};

// 初始尝试初始化（使用 .env 配置）
if (isSupabaseConfigured()) {
  client = initializeClient();
}

export const supabase = client;

/**
 * 使用新的配置重新初始化 Supabase 客户端
 * @param url Supabase URL
 * @param anonKey Supabase Anon Key
 * @returns 新的 Supabase 客户端或 null
 */
export const reinitializeSupabaseClient = (url: string, anonKey: string): SupabaseClient | null => {
  configuredSupabaseUrl = url;
  configuredSupabaseAnonKey = anonKey;

  if (!isSupabaseConfigured()) {
    return null;
  }

  client = initializeClient();
  return client;
};

/**
 * 获取当前的 Supabase 客户端
 * 用于在重新初始化后获取最新的客户端实例
 */
export const getSupabaseClient = (): SupabaseClient | null => {
  return client;
};

/**
 * 获取当前配置的 Supabase 凭据
 */
export const getSupabaseCredentials = (): { url: string | null; anonKey: string | null } => {
  return {
    url: configuredSupabaseUrl || getEnv('VITE_SUPABASE_URL') || getEnv('REACT_APP_SUPABASE_URL') || null,
    anonKey: configuredSupabaseAnonKey || getEnv('VITE_SUPABASE_ANON_KEY') || getEnv('REACT_APP_SUPABASE_ANON_KEY') || null,
  };
};

// Helper to get Supabase session token for API calls
export const getSupabaseAccessToken = async (): Promise<string | null> => {
  const currentClient = getSupabaseClient();
  if (!currentClient) return null;
  try {
    const { data: { session } } = await currentClient.auth.getSession();
    return session?.access_token || null;
  } catch (e) {
    console.error('Failed to get Supabase session:', e);
    return null;
  }
};
