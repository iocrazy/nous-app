import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Project, ProjectFile, RecentItem } from '../types';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { useWorkspaceScope } from '../hooks/useWorkspaceScope';
import { fetchProjects, fetchRecentItems } from '../services/projectsService';
import { ProjectsListView } from '../components/ProjectsListView';
import { ProjectFilterSidebar } from '../components/project/ProjectFilterSidebar';
import { RecentItemsList } from '../components/project/RecentItemsList';
import { VideoReviewPage } from '../components/VideoReviewPage';
import { CreateProjectModal } from '../components/CreateProjectModal';
import { ProjectWorkspace } from '../components/workspace/ProjectWorkspace';

export function ProjectsPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { teamId, projectId } = useParams();
  const [, setSearchParams] = useSearchParams();
  const { currentUserId } = useAuth();
  const { selectedTeamId, personalTeamId } = useTeamContext();
  // Default the create-project Team selector to the active workspace: a real
  // collaborative team pre-selects itself, personal stays "Personal" (empty →
  // team_id=NULL, the projects personal convention). Snowflake ids stay strings.
  const { isPersonal, effectiveTeamId } = useWorkspaceScope();
  const createDefaultTeamId = isPersonal ? '' : effectiveTeamId;

  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [reviewFile, setReviewFile] = useState<ProjectFile | null>(null);
  const [isCreateProjectModalOpen, setIsCreateProjectModalOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeFilter, setActiveFilter] = useState('all');
  const [recentItems, setRecentItems] = useState<RecentItem[]>([]);
  const [recentLoaded, setRecentLoaded] = useState(false);

  // Load projects + auto-select from URL
  useEffect(() => {
    const load = async () => {
      try {
        const isPersonal = !selectedTeamId || selectedTeamId === personalTeamId;
        const data = await fetchProjects({
          teamId: isPersonal ? 'personal' : selectedTeamId,
        });
        setProjects(data);
        // Auto-select project from URL param
        if (projectId && !selectedProject) {
          const match = data.find(p => String(p.id) === projectId);
          if (match) setSelectedProject(match);
        }
      } catch (err) {
        console.error('Failed to load projects:', err);
      }
    };
    load();
  }, [projectId, selectedTeamId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Recent feed loader, shared by the eager mount fetch and the
  // refresh-on-activate effect below. `recentInFlightRef` guards against
  // both effects firing a redundant overlapping request in the same tick
  // (e.g. the team scope changes while the Recent view is already active).
  // `mountedRef` avoids setState after unmount if the request resolves late.
  const recentInFlightRef = useRef(false);
  const mountedRef = useRef(true);
  useEffect(() => () => {
    mountedRef.current = false;
  }, []);
  const loadRecentItems = useCallback(() => {
    if (recentInFlightRef.current) return;
    recentInFlightRef.current = true;
    fetchRecentItems(8)
      .then((items) => {
        if (!mountedRef.current) return;
        setRecentItems(items);
        setRecentLoaded(true);
      })
      .catch((err) => {
        console.error('Failed to load recent items:', err);
        if (!mountedRef.current) return;
        setRecentItems([]);
        setRecentLoaded(true);
      })
      .finally(() => {
        recentInFlightRef.current = false;
      });
  }, []);

  // Eager fetch on mount (+ whenever the team scope changes) so the
  // sidebar "Recent" count is correct immediately — the user shouldn't have
  // to click into the Recent view first just to see how many items are
  // there. `limit=8` keeps this a cheap single request.
  useEffect(() => {
    loadRecentItems();
  }, [selectedTeamId, loadRecentItems]);

  // Refresh when the Recent view is activated, in case the eager fetch
  // above is now stale (e.g. a script/canvas was edited elsewhere since
  // mount). `recentInFlightRef` prevents this from double-firing alongside
  // the mount effect above.
  useEffect(() => {
    if (activeFilter !== 'recent') return;
    loadRecentItems();
  }, [activeFilter, loadRecentItems]);

  // Derive filter counts and folders
  const starredProjects = useMemo(() => projects.filter(p => p.is_starred), [projects]);

  const folders = useMemo(() => {
    const groups = new Set<string>();
    for (const p of projects) {
      const g = p.project_group?.trim();
      if (g) groups.add(g);
    }
    return [...groups].sort();
  }, [projects]);

  const projectCounts = useMemo(() => ({
    recent: recentLoaded ? recentItems.length : 0,
    all: projects.length,
    starred: starredProjects.length,
    archived: projects.filter(p => !!p.archived_at).length,
  }), [projects, starredProjects, recentItems, recentLoaded]);

  const folderCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of projects) {
      const g = p.project_group?.trim();
      if (g) counts[g] = (counts[g] || 0) + 1;
    }
    return counts;
  }, [projects]);

  // Filter projects for card view
  // PR-9 (G7): Views rail reduced to All / Starred / Archived.
  const filteredProjects = useMemo(() => {
    switch (activeFilter) {
      case 'starred': return starredProjects;
      case 'archived': return projects.filter(p => !!p.archived_at);
      default:
        // Check if it's a folder filter
        if (folders.includes(activeFilter)) {
          return projects.filter(p => p.project_group?.trim() === activeFilter);
        }
        return projects;
    }
  }, [activeFilter, projects, starredProjects, folders]);

  const filterTitle = useMemo(() => {
    switch (activeFilter) {
      case 'recent': return t('projects.view.recent');
      case 'starred': return t('projects.filterTitle.starred');
      case 'archived': return t('projects.filterTitle.archived');
      default:
        if (folders.includes(activeFilter)) return activeFilter;
        return t('projects.title', 'All Projects');
    }
  }, [activeFilter, folders, t]);

  // Handlers
  const handleProjectSelect = useCallback((project: Project) => {
    setSelectedProject(project);
    setSearchParams({});
    navigate(teamId ? `/team/${teamId}/projects/${project.id}` : `/projects/${project.id}`);
  }, [navigate, teamId, setSearchParams]);

  const handleBackToList = useCallback(() => {
    setSelectedProject(null);
    navigate(teamId ? `/team/${teamId}/projects` : '/projects');
  }, [navigate, teamId]);

  // Recent-row navigation. `/projects/recent-items` is owner-scoped across
  // EVERY team the caller belongs to (not just the team currently open in
  // this page), so the target URL MUST be built from the item's OWN
  // `team_id` — never the active page's `teamId` / `effectiveTeamId` — or a
  // cross-team item silently fails to open (it's not in the current page's
  // project list, since that list only ever fetches the active team's
  // projects). A personal project carries `team_id: null`; fall back to the
  // personal-team snowflake, mirroring the existing canvas convention
  // ("personal falls back to the personal-team snowflake").
  //
  // Canvas → the standalone canvas editor (/team/:teamId/canvas/:canvasId —
  // always team-scoped). Script → the project workspace with the Script
  // module preselected (the workspace URL contract only targets a module
  // via ?module=, not a specific script/episode — it auto-resolves the
  // active episode's script itself).
  const handleRecentSelect = useCallback((item: RecentItem) => {
    const targetTeamId = item.team_id || personalTeamId;
    if (item.kind === 'canvas') {
      navigate(`/team/${targetTeamId}/canvas/${item.id}`);
      return;
    }
    navigate(`/team/${targetTeamId}/projects/${item.project_id}?module=script`);
  }, [navigate, personalTeamId]);

  const refreshProjects = useCallback(async () => {
    try {
      // Mirror the initial-load effect's team derivation — without this,
      // any context-menu action (star/color/archive/delete) would refetch
      // with no team filter and silently reset the list to all-teams.
      const isPersonal = !selectedTeamId || selectedTeamId === personalTeamId;
      const data = await fetchProjects({
        teamId: isPersonal ? 'personal' : selectedTeamId,
      });
      setProjects(data);
    } catch (err) {
      console.error('Failed to refresh projects:', err);
    }
  }, [selectedTeamId, personalTeamId]);

  // ─── File Review ─────────────────────────────────────────────
  if (reviewFile && selectedProject) {
    return (
      <VideoReviewPage
        projectId={selectedProject.id}
        file={reviewFile}
        onBack={() => {
          setReviewFile(null);
          navigate(teamId ? `/team/${teamId}/projects/${selectedProject.id}` : `/projects/${selectedProject.id}`);
        }}
        currentUserId={currentUserId || ''}
      />
    );
  }

  // ─── Project Detail View ────────────────────────────────────
  // The workspace shell owns the entire detail pane (its own sidebar + top
  // bar + module content). It is now the only detail implementation — the
  // legacy nav sidebar + stage strip + tab content was retired in PR-18.
  if (selectedProject) {
    return (
      <ProjectWorkspace
        project={selectedProject}
        teamId={teamId}
        onBack={handleBackToList}
        onProjectUpdated={(updated) => {
          setSelectedProject(updated);
          refreshProjects();
        }}
      />
    );
  }

  // ─── Project List View ──────────────────────────────────────
  return (
    <>
      <div
        className={'flex h-full min-h-0'}
      >
        <ProjectFilterSidebar
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          folders={folders}
          projectCounts={projectCounts}
          folderCounts={folderCounts}
          onCreateProject={() => setIsCreateProjectModalOpen(true)}
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        />
        <div className={'flex-1 min-w-0 h-full overflow-y-auto px-8 pt-3 pb-8'}>
          {activeFilter === 'recent' ? (
            <>
              <h2 className="text-lg font-semibold text-ink-100 mb-4">{filterTitle}</h2>
              <RecentItemsList items={recentItems} onSelect={handleRecentSelect} />
            </>
          ) : (
            <ProjectsListView
              projects={filteredProjects}
              onProjectSelect={handleProjectSelect}
              onCreateProject={() => setIsCreateProjectModalOpen(true)}
              onProjectsChange={refreshProjects}
              title={filterTitle}
            />
          )}
        </div>
      </div>
      <CreateProjectModal
        isOpen={isCreateProjectModalOpen}
        onClose={() => setIsCreateProjectModalOpen(false)}
        defaultTeamId={createDefaultTeamId}
        onProjectCreated={(project) => {
          setIsCreateProjectModalOpen(false);
          setSelectedProject(project);
          refreshProjects();
          navigate(teamId ? `/team/${teamId}/projects/${project.id}` : `/projects/${project.id}`);
        }}
      />
    </>
  );
}
