import { useState, useEffect, useMemo, useCallback } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Project, ProjectFile } from '../types';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { fetchProjects } from '../services/projectsService';
import { ProjectsListView } from '../components/ProjectsListView';
import { ProjectFilterSidebar } from '../components/project/ProjectFilterSidebar';
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

  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [reviewFile, setReviewFile] = useState<ProjectFile | null>(null);
  const [isCreateProjectModalOpen, setIsCreateProjectModalOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeFilter, setActiveFilter] = useState('all');

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

  // PR-9 (G7): Views rail reduced to All / Starred / Archived.
  const projectCounts = useMemo(() => ({
    all: projects.length,
    starred: starredProjects.length,
    archived: projects.filter(p => !!p.archived_at).length,
  }), [projects, starredProjects]);

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
          <ProjectsListView
            projects={filteredProjects}
            onProjectSelect={handleProjectSelect}
            onCreateProject={() => setIsCreateProjectModalOpen(true)}
            onProjectsChange={refreshProjects}
            title={filterTitle}
          />
        </div>
      </div>
      <CreateProjectModal
        isOpen={isCreateProjectModalOpen}
        onClose={() => setIsCreateProjectModalOpen(false)}
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
