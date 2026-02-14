import React, { createContext, useContext, useState, useEffect } from 'react';
import { getSupabaseClient, isSupabaseConfigured, reinitializeSupabaseClient, getSupabaseCredentials } from '../supabaseClient';
import { UserProfile, UserSettings, AISettings as AISettingsType } from '../types';
import { fetchUserSettings, saveUserSettings, fetchFrontendConfig, saveFrontendConfig } from '../services/dataService';

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
};

const DEFAULT_AI_SETTINGS: AISettingsType = {
  ai_enabled: false,
  auto_transcribe: false,
  auto_summarize: false,
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

  // Load frontend config from backend YAML on mount
  useEffect(() => {
    const loadFrontendConfig = async () => {
      try {
        const config = await fetchFrontendConfig();
        if (config) {
          if (config.supabase_url && config.supabase_anon_key) {
            reinitializeSupabaseClient(config.supabase_url, config.supabase_anon_key);
            setUserSettings(prev => ({
              ...prev,
              supabaseUrl: config.supabase_url || '',
              supabaseAnonKey: config.supabase_anon_key || '',
              downloadPath: config.default_download_path || prev.downloadPath,
            }));
          } else if (config.default_download_path) {
            setUserSettings(prev => ({
              ...prev,
              downloadPath: config.default_download_path || prev.downloadPath,
            }));
          }
        }
      } catch (err) {
        console.error('Failed to load frontend config:', err);
      } finally {
        setIsConfigLoaded(true);
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

    const handleSession = async (session: { user: { id: string; email?: string | null } } | null) => {
      if (session?.user) {
        setUserProfile(prev => ({
          ...prev,
          name: session.user.email?.split('@')[0] || 'User',
          email: session.user.email || '',
        }));
        setCurrentUserId(session.user.id);
        setIsAuthenticated(true);

        try {
          const { data: profile } = await supabase
            .from('user_profiles')
            .select('role')
            .eq('id', session.user.id)
            .single();
          if (profile?.role) {
            setUserProfile(prev => ({ ...prev, role: profile.role }));
          }
        } catch (err) {
          console.debug('Failed to load user role:', err);
        }

        try {
          const settings = await fetchUserSettings();
          if (settings) {
            setUserSettings(prev => ({
              ...prev,
              downloadPath: settings.download_path || prev.downloadPath,
            }));
          }
        } catch (err) {
          console.error('Failed to load user settings:', err);
        }
      } else {
        setIsAuthenticated(false);
        setCurrentUserId(null);
      }
    };

    supabase.auth.getSession().then(({ data: { session } }) => {
      handleSession(session);
      setIsAuthLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        console.log('Auth state changed:', event);
        if (event === 'SIGNED_OUT') {
          setIsAuthenticated(false);
          setCurrentUserId(null);
          onLogout?.();
        } else if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED') {
          handleSession(session);
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
    const supabase = getSupabaseClient();
    if (isSupabaseConfigured() && supabase) {
      await supabase.auth.signOut();
    }
    setIsAuthenticated(false);
    setIsProfileModalOpen(false);
    setUserProfile(DEFAULT_PROFILE);
    setCurrentUserId(null);
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
      });

      if (isAuthenticated && isSupabaseConfigured()) {
        try {
          await saveUserSettings({
            download_path: newSettings.downloadPath
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
