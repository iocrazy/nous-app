import { useState, useEffect, useMemo, useCallback } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Project, ProjectFile, ProjectTab } from '../types';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { fetchProjects } from '../services/projectsService';
import { ProjectsListView } from '../components/ProjectsListView';
import { ProjectFilterSidebar } from '../components/project/ProjectFilterSidebar';
import { ProjectNavSidebar } from '../components/project/ProjectNavSidebar';
import { ProjectFilesView } from '../components/ProjectFilesView';
import { VideoReviewPage } from '../components/VideoReviewPage';
import { CreateProjectModal } from '../components/CreateProjectModal';
import { KanbanBoard } from '../components/KanbanBoard';
import { ProjectTrashView } from '../components/ProjectTrashView';
import { ProjectSharesView } from '../components/ProjectSharesView';
import { ProjectStoryboardTab } from '../components/project/ProjectStoryboardTab';
import { ProjectScriptsTab } from '../components/project/ProjectScriptsTab';
import { ProjectOutputTab } from '../components/project/ProjectOutputTab';

// Map URL tab param → ProjectNavSidebar section key
const TAB_TO_SECTION: Record<string, string> = {
  files: 'files',
  scripts: 'scripts',
  storyboard: 'storyboard',
  output: 'output',
  tasks: 'tasks',
  shares: 'shares',
  trash: 'trash',
};

export function ProjectsPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { teamId, projectId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { currentUserId } = useAuth();
  const { selectedTeamId } = useTeamContext();

  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [reviewFile, setReviewFile] = useState<ProjectFile | null>(null);
  const [isCreateProjectModalOpen, setIsCreateProjectModalOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [shareCount, setShareCount] = useState(0);
  const [trashCount, setTrashCount] = useState(0);
  const [activeFilter, setActiveFilter] = useState('all');

  // Active tab from URL
  const activeTab: ProjectTab = (searchParams.get('tab') as ProjectTab) || 'files';

  const setActiveTab = useCallback((tab: ProjectTab) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (tab === 'files') {
        next.delete('tab');
      } else {
        next.set('tab', tab);
      }
      return next;
    });
  }, [setSearchParams]);

  // Load projects + auto-select from URL
  useEffect(() => {
    const load = async () => {
      try {
        const data = await fetchProjects();
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
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Derive filter counts and folders
  const starredProjects = useMemo(() => projects.filter(p => p.is_starred), [projects]);
  const recentProjects = useMemo(() =>
    [...projects].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()).slice(0, 8),
    [projects]
  );

  const folders = useMemo(() => {
    const groups = new Set<string>();
    for (const p of projects) {
      const g = p.project_group?.trim();
      if (g) groups.add(g);
    }
    return [...groups].sort();
  }, [projects]);

  const projectCounts = useMemo(() => ({
    all: projects.length,
    starred: starredProjects.length,
    recent: recentProjects.length,
    active: projects.filter(p => p.status !== 'archived').length,
    archived: projects.filter(p => p.status === 'archived').length,
  }), [projects, starredProjects, recentProjects]);

  const folderCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of projects) {
      const g = p.project_group?.trim();
      if (g) counts[g] = (counts[g] || 0) + 1;
    }
    return counts;
  }, [projects]);

  // Filter projects for card view
  const filteredProjects = useMemo(() => {
    switch (activeFilter) {
      case 'starred': return starredProjects;
      case 'recent': return recentProjects;
      case 'active': return projects.filter(p => p.status !== 'archived');
      case 'archived': return projects.filter(p => p.status === 'archived');
      default:
        // Check if it's a folder filter
        if (folders.includes(activeFilter)) {
          return projects.filter(p => p.project_group?.trim() === activeFilter);
        }
        return projects;
    }
  }, [activeFilter, projects, starredProjects, recentProjects, folders]);

  const filterTitle = useMemo(() => {
    switch (activeFilter) {
      case 'starred': return 'Starred Projects';
      case 'recent': return 'Recent Projects';
      case 'active': return 'Active Projects';
      case 'archived': return 'Archived Projects';
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
      const data = await fetchProjects();
      setProjects(data);
    } catch (err) {
      console.error('Failed to refresh projects:', err);
    }
  }, []);

  const handleSectionChange = useCallback((section: string) => {
    setActiveTab(section as ProjectTab);
  }, [setActiveTab]);

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
  if (selectedProject) {
    const sectionCounts: Record<string, number> = {};
    if (shareCount > 0) sectionCounts.shares = shareCount;
    if (trashCount > 0) sectionCounts.trash = trashCount;

    return (
      <div className="flex -mx-4 -mt-14 -mb-20 sm:-mx-8 sm:-mt-20 sm:-mb-8" style={{ height: '100vh' }}>
        <ProjectNavSidebar
          project={selectedProject}
          activeSection={TAB_TO_SECTION[activeTab] || 'files'}
          onSectionChange={handleSectionChange}
          onBackToList={handleBackToList}
          recentProjects={recentProjects}
          starredProjects={starredProjects}
          onProjectSwitch={handleProjectSelect}
          sectionCounts={sectionCounts}
        />
        <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden animate-in fade-in duration-300">
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {activeTab === 'files' && (
              <ProjectFilesView
                project={selectedProject}
                onBack={handleBackToList}
                onFileReview={(file) => {
                  setReviewFile(file);
                  navigate(teamId ? `/team/${teamId}/projects/${selectedProject.id}/review/${file.id}` : `/projects/${selectedProject.id}/review/${file.id}`);
                }}
              />
            )}
            {activeTab === 'scripts' && <ProjectScriptsTab projectId={selectedProject.id} />}
            {activeTab === 'storyboard' && <ProjectStoryboardTab projectId={selectedProject.id} />}
            {activeTab === 'output' && <ProjectOutputTab projectId={selectedProject.id} />}
            {activeTab === 'tasks' && <KanbanBoard projectId={selectedProject.id} teamId={selectedTeamId || undefined} />}
            {activeTab === 'shares' && <ProjectSharesView projectId={selectedProject.id} onCountChange={setShareCount} />}
            {activeTab === 'trash' && <ProjectTrashView projectId={selectedProject.id} onCountChange={setTrashCount} />}
          </div>
        </div>
      </div>
    );
  }

  // ─── Project List View ──────────────────────────────────────
  return (
    <>
      <div className="flex -mx-4 -mt-14 -mb-20 sm:-mx-8 sm:-mt-20 sm:-mb-8 animate-in fade-in slide-in-from-bottom-4 duration-500" style={{ height: '100vh' }}>
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
        <div className="flex-1 min-w-0 px-6 py-4">
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
