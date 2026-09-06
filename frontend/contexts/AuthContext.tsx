import React, { createContext, useContext, useState, useEffect } from 'react';
import { getSupabaseClient, isSupabaseConfigured, reinitializeSupabaseClient, getSupabaseCredentials } from '../supabaseClient';
import { UserProfile, UserSettings, AISettings as AISettingsType } from '../types';
import { fetchUserSettings, saveUserSettings, fetchFrontendConfig, saveFrontendConfig } from '../services/dataService';
import { createMediaSession, deleteMediaSession, fetchMediaToken } from '../services/mediaAuthService';
import { installAuthRecovery } from '../services/authRecovery';

interface AuthState {
  isAuthenticated: boolean;
  isAuthLoading: boolean;
  currentUserId: string | null;
  showAuthModal: boolean;
  isConfigLoaded: boolean;
  userProfile: UserProfile;
  userSettings: UserSettings;
  aiSettings: AISettingsType;
  isProfileModalOpen: boolean;
  mediaToken: string | null;
}

interface AuthActions {
  setShowAuthModal: (show: boolean) => void;
  setIsProfileModalOpen: (open: boolean) => void;
  setUserProfile: React.Dispatch<React.SetStateAction<UserProfile>>;
  setAISettings: React.Dispatch<React.SetStateAction<AISettingsType>>;
  handleLogin: (user: { email: string; id: string }) => void;
  handleLogout: () => Promise<void>;
  handleUpdateSettings: (newSettings: UserSettings) => Promise<void>;
}

type AuthContextValue = AuthState & AuthActions;

const AuthContext = createContext<AuthContextValue | null>(null);

const DEFAULT_PROFILE: UserProfile = {
  name: 'Demo User',
  email: 'demo@example.com',
  avatarUrl: '',
  plan: 'Free Plan',
};

const DEFAULT_SETTINGS: UserSettings = {
  downloadPath: '/home/user/downloads/mediahub',
  supabaseUrl: '',
  supabaseAnonKey: '',
  maxConcurrentDownloads: 3,
};

const DEFAULT_AI_SETTINGS: AISettingsType = {
  ai_enabled: false,
  preferred_language: 'auto',
  providers: {},
  task_assignment: {
    transcription: '',
    summarization: '',
    visual_analysis: '',
  },
};

export function AuthProvider({
  children,
  onLogout,
}: {
  children: React.ReactNode;
  onLogout?: () => void;
}) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isAuthLoading, setIsAuthLoading] = useState(true);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [isConfigLoaded, setIsConfigLoaded] = useState(false);
  const [userProfile, setUserProfile] = useState<UserProfile>(DEFAULT_PROFILE);
  const [userSettings, setUserSettings] = useState<UserSettings>(DEFAULT_SETTINGS);
  const [aiSettings, setAISettings] = useState<AISettingsType>(DEFAULT_AI_SETTINGS);
  const [isProfileModalOpen, setIsProfileModalOpen] = useState(false);
  const [mediaToken, setMediaToken] = useState<string | null>(null);

  // Load frontend config from backend YAML on mount.
  // If Supabase is already configured via env vars, mark config as loaded
  // immediately so auth init is not blocked by a slow API call.
  useEffect(() => {
    const alreadyConfigured = isSupabaseConfigured();
    if (alreadyConfigured) {
      setIsConfigLoaded(true);
    }

    const loadFrontendConfig = async () => {
      try {
        const config = await fetchFrontendConfig();
        if (config) {
          const transcodeUpdates: Partial<typeof DEFAULT_SETTINGS> = {};
          if (config.transcode_enabled != null) transcodeUpdates.transcodeEnabled = config.transcode_enabled;
          if (config.transcode_tiers != null) transcodeUpdates.transcodeTiers = config.transcode_tiers;
          if (config.ffmpeg_encoder != null) transcodeUpdates.ffmpegEncoder = config.ffmpeg_encoder;
          if (config.ffmpeg_preset != null) transcodeUpdates.ffmpegPreset = config.ffmpeg_preset;
          if (config.transcode_parallel_tiers != null) transcodeUpdates.transcodeParallelTiers = config.transcode_parallel_tiers;

          if (config.supabase_url && config.supabase_anon_key && !alreadyConfigured) {
            reinitializeSupabaseClient(config.supabase_url, config.supabase_anon_key);
            setUserSettings(prev => ({
              ...prev,
              supabaseUrl: config.supabase_url || '',
              supabaseAnonKey: config.supabase_anon_key || '',
              downloadPath: config.default_download_path || prev.downloadPath,
              ...transcodeUpdates,
            }));
          } else {
            setUserSettings(prev => ({
              ...prev,
              downloadPath: config.default_download_path || prev.downloadPath,
              ...transcodeUpdates,
            }));
          }
        }
      } catch (err) {
        console.error('Failed to load frontend config:', err);
      } finally {
        if (!alreadyConfigured) {
          setIsConfigLoaded(true);
        }
      }
    };
    loadFrontendConfig();
  }, []);

  // Check for existing session and listen for auth state changes
  useEffect(() => {
    if (!isConfigLoaded) return;

    const supabase = getSupabaseClient();
    if (!isSupabaseConfigured() || !supabase) {
      setIsAuthLoading(false);
      return;
    }

    const credentials = getSupabaseCredentials();
    setUserSettings(prev => ({
      ...prev,
      supabaseUrl: credentials.url || prev.supabaseUrl,
      supabaseAnonKey: credentials.anonKey || prev.supabaseAnonKey,
    }));

    const handleSession = async (session: { access_token?: string; user: { id: string; email?: string | null; user_metadata?: Record<string, unknown> } } | null) => {
      if (session?.user) {
        const displayName =
          (session.user.user_metadata?.display_name as string) ||
          (session.user.user_metadata?.full_name as string) ||
          (session.user.user_metadata?.name as string) ||
          session.user.email?.split('@')[0] ||
          'User';
        setUserProfile(prev => ({
          ...prev,
          name: displayName,
          email: session.user.email || '',
        }));
        setCurrentUserId(session.user.id);

        // Set media session cookie BEFORE enabling auth state,
        // so video/image loads already have the cookie when components render
        if (session.access_token) {
          try {
            await createMediaSession(session.access_token);
          } catch (err) {
            console.error('Failed to create media session:', err);
          }
        }

        setIsAuthenticated(true);

        // Fetch signed media token for URL-based auth (non-blocking)
        if (session.access_token) {
          fetchMediaToken(session.access_token).then(tok => {
            if (tok) setMediaToken(tok);
          });
        }

        // Load profile and settings in background — don't block data loading
        // ``.maybeSingle()`` returns null instead of 406 when the row is
        // missing. user_profiles can lag auth.users for legacy users whose
        // profile-creation trigger didn't fire (e.g. accounts created
        // before the trigger was added, or migration backfill misses).
        supabase
          .from('user_profiles')
          .select('role')
          .eq('id', session.user.id)
          .maybeSingle()
          .then(({ data: profile }) => {
            if (profile?.role) {
              setUserProfile(prev => ({ ...prev, role: profile.role }));
            }
          })
          .catch((err) => console.debug('Failed to load user role:', err));

        fetchUserSettings()
          .then((settings) => {
            if (settings) {
              const sj = (settings.settings_json || {}) as Record<string, unknown>;
              const cap = sj.maxConcurrentDownloads;
              setUserSettings(prev => ({
                ...prev,
                downloadPath: settings.download_path || prev.downloadPath,
                ...(typeof cap === 'number'
                  ? { maxConcurrentDownloads: cap }
                  : {}),
              }));
            }
          })
          .catch((err) => console.error('Failed to load user settings:', err));

        // Load AI settings
        import('../services/aiService').then(({ getAISettings }) => {
          getAISettings()
            .then((ai) => { if (ai) setAISettings(ai); })
            .catch((err) => console.debug('Failed to load AI settings:', err));
        });
      } else {
        setIsAuthenticated(false);
        setCurrentUserId(null);
      }
    };

    // Global 401 recovery: refresh-or-logout when the backend rejects a
    // dead token (slept tab whose silent refresh failed). On logout it
    // fires SIGNED_OUT -> isAuthenticated=false -> AuthGuard /login.
    installAuthRecovery();

    supabase.auth.getSession().then(async ({ data: { session } }) => {
      await handleSession(session);
      setIsAuthLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        if (event === 'SIGNED_OUT') {
          setIsAuthenticated(false);
          setCurrentUserId(null);
          onLogout?.();
        } else if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED') {
          await handleSession(session);
        }
      }
    );

    return () => {
      subscription.unsubscribe();
    };
  }, [isConfigLoaded]);

  const handleLogin = (user: { email: string; id: string }) => {
    setUserProfile(prev => ({
      ...prev,
      name: user.email.split('@')[0] || 'User',
      email: user.email,
    }));
    setCurrentUserId(user.id);
    setIsAuthenticated(true);
    setShowAuthModal(false);
  };

  const handleLogout = async () => {
    // Clear media session cookie before signing out
    await deleteMediaSession();

    const supabase = getSupabaseClient();
    if (isSupabaseConfigured() && supabase) {
      await supabase.auth.signOut();
    }
    setIsAuthenticated(false);
    setIsProfileModalOpen(false);
    setUserProfile(DEFAULT_PROFILE);
    setCurrentUserId(null);
    setMediaToken(null);
    setShowAuthModal(false);
    onLogout?.();
  };

  const handleUpdateSettings = async (newSettings: UserSettings) => {
    const dbChanged =
      newSettings.supabaseUrl !== userSettings.supabaseUrl ||
      newSettings.supabaseAnonKey !== userSettings.supabaseAnonKey;

    try {
      await saveFrontendConfig({
        supabase_url: newSettings.supabaseUrl || undefined,
        supabase_anon_key: newSettings.supabaseAnonKey || undefined,
        default_download_path: newSettings.downloadPath,
        transcode_enabled: newSettings.transcodeEnabled,
        transcode_tiers: newSettings.transcodeTiers,
        ffmpeg_encoder: newSettings.ffmpegEncoder,
        ffmpeg_preset: newSettings.ffmpegPreset,
        transcode_parallel_tiers: newSettings.transcodeParallelTiers,
      });

      if (isAuthenticated && isSupabaseConfigured()) {
        try {
          // settings_json carries per-user General preferences the backend
          // reads. maxConcurrentDownloads drives the live batch-concurrency
          // cap (backend emits config.parse_concurrency on save → no restart).
          const settingsJson: Record<string, unknown> = {};
          if (typeof newSettings.maxConcurrentDownloads === 'number') {
            settingsJson.maxConcurrentDownloads = newSettings.maxConcurrentDownloads;
          }
          await saveUserSettings({
            download_path: newSettings.downloadPath,
            ...(Object.keys(settingsJson).length > 0
              ? { settings_json: settingsJson }
              : {}),
          });
        } catch (err) {
          console.error('Failed to save settings to Supabase:', err);
        }
      }

      setUserSettings(newSettings);

      if (dbChanged) {
        if (confirm("Database configuration changed. Application must reload to apply changes. Reload now?")) {
          window.location.reload();
        }
      } else {
        alert("Settings saved successfully!");
      }
    } catch (err) {
      console.error('Failed to save settings:', err);
      alert("Failed to save settings. Please try again.");
    }
  };

  const value: AuthContextValue = {
    isAuthenticated,
    isAuthLoading,
    currentUserId,
    showAuthModal,
    isConfigLoaded,
    userProfile,
    userSettings,
    aiSettings,
    isProfileModalOpen,
    mediaToken,
    setShowAuthModal,
    setIsProfileModalOpen,
    setUserProfile,
    setAISettings,
    handleLogin,
    handleLogout,
    handleUpdateSettings,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

/** Like `useAuth`, but `null` outside the provider instead of throwing — for
 *  leaf components whose only need is an optional value (the media token on
 *  an `<img>`), so they still render in isolation. */
export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext);
}
