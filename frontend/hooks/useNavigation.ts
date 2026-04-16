import { useState, useEffect, useRef } from 'react';
import { ViewState, Project, ProjectFile, SidebarMode } from '../types';
import { fetchDashboardStats, DashboardStats } from '../services/dataService';
import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';

interface UseNavigationParams {
  isAuthenticated: boolean;
  selectedTeamId: string | null;
  personalTeamId: string | null;
}

export function useNavigation({ isAuthenticated, selectedTeamId, personalTeamId }: UseNavigationParams) {
  const [view, setView] = useState<ViewState>('parser');
  const [settingsTab, setSettingsTab] = useState<'general' | 'api' | 'logs' | 'monitor' | 'tasks' | 'tags' | 'ai' | 'docs'>('general');

  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [reviewFile, setReviewFile] = useState<ProjectFile | null>(null);
  const [isCreateProjectModalOpen, setIsCreateProjectModalOpen] = useState(false);

  const [isLibraryOpen, setIsLibraryOpen] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);
  const [dashboardSubView, setDashboardSubView] = useState<'overview' | 'tasks' | 'logs' | 'monitor' | 'nous-models' | 'deployment-logs'>('overview');
  const [isDashboardMenuOpen, setIsDashboardMenuOpen] = useState(false);

  // Dashboard Stats
  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(null);

  // Sidebar mode: personal team is still a team record (is_personal=true)
  // Gate 'team' on personalTeamId being loaded to avoid flash on refresh
  const sidebarMode: SidebarMode = selectedProject && selectedTeamId ? 'project'
    : (personalTeamId && selectedTeamId === personalTeamId) ? 'personal'
    : (personalTeamId && selectedTeamId) ? 'team'
    : 'personal';

  // Accordion behavior
  const toggleLibraryMenu = () => {
    const newState = !isLibraryOpen;
    setIsLibraryOpen(newState);
    if (newState) setIsSettingsOpen(false);
  };
  const toggleSettingsMenu = () => {
    const newState = !isSettingsOpen;
    setIsSettingsOpen(newState);
    if (newState) setIsLibraryOpen(false);
  };

  // Lazy-load dashboard stats
  useEffect(() => {
    if (!isAuthenticated || view !== 'dashboard' || dashboardSubView !== 'overview') return;
    let cancelled = false;
    const loadStats = async () => {
      try {
        const stats = await fetchDashboardStats();
        if (!cancelled) setDashboardStats(stats);
      } catch (e) {
        console.debug('Failed to load dashboard stats:', e);
      }
    };
    loadStats();
    return () => { cancelled = true; };
  }, [isAuthenticated, view, dashboardSubView]);

  // Realtime for user_logs → dashboard recent activity
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!isAuthenticated || !isSupabaseConfigured() || !supabase) return;

    const logsChannel = supabase
      .channel('dashboard_logs_realtime')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'user_logs' },
        (payload) => {
          setDashboardStats(prev => {
            if (!prev) return prev;
            const row = payload.new as { message?: string; created_at?: string; status?: string };
            const newEntry = {
              message: row.message || '',
              time: row.created_at
                ? new Date(row.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
                : '',
              status: row.status === 'success' ? 'success' as const
                : row.status === 'error' ? 'error' as const
                : 'pending' as const,
            };
            return { ...prev, recentLogs: [newEntry, ...prev.recentLogs].slice(0, 10) };
          });
        }
      )
      .subscribe();

    return () => { supabase.removeChannel(logsChannel); };
  }, [isAuthenticated]);

  // Auto-select first team
  const hasAutoSelected = useRef(false);

  return {
    view, setView,
    settingsTab, setSettingsTab,
    selectedProject, setSelectedProject,
    reviewFile, setReviewFile,
    isCreateProjectModalOpen, setIsCreateProjectModalOpen,
    isLibraryOpen, isSettingsOpen,
    isMobileMenuOpen, setIsMobileMenuOpen,
    isMobileSearchOpen, setIsMobileSearchOpen,
    dashboardSubView, setDashboardSubView,
    isDashboardMenuOpen, setIsDashboardMenuOpen,
    dashboardStats, setDashboardStats,
    sidebarMode,
    toggleLibraryMenu, toggleSettingsMenu,
    hasAutoSelected,
  };
}
