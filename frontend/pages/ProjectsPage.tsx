import { useState, useEffect } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { FolderOpen, KanbanSquare } from 'lucide-react';
import { Project, ProjectFile } from '../types';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { fetchProjects } from '../services/projectsService';
import { ProjectsListView } from '../components/ProjectsListView';
import { ProjectsSidebar } from '../components/ProjectsSidebar';
import { ProjectFilesView } from '../components/ProjectFilesView';
import { VideoReviewPage } from '../components/VideoReviewPage';
import { CreateProjectModal } from '../components/CreateProjectModal';
import { KanbanBoard } from '../components/KanbanBoard';

type ProjectTab = 'files' | 'tasks';

export function ProjectsPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { teamId, projectId, fileId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { currentUserId } = useAuth();
  const { selectedTeamId } = useTeamContext();

  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [reviewFile, setReviewFile] = useState<ProjectFile | null>(null);
  const [isCreateProjectModalOpen, setIsCreateProjectModalOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);

  // Read active tab from URL query params, default to 'files'
  const activeTab: ProjectTab = (searchParams.get('tab') as ProjectTab) || 'files';

  const setActiveTab = (tab: ProjectTab) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (tab === 'files') {
        next.delete('tab');
      } else {
        next.set('tab', tab);
      }
      return next;
    });
  };

  // Load projects for sidebar
  useEffect(() => {
    const load = async () => {
      try {
        const data = await fetchProjects();
        setProjects(data);
      } catch (err) {
        console.error('Failed to load projects for sidebar:', err);
      }
    };
    load();
  }, []);

  const handleProjectSelect = (project: Project) => {
    setSelectedProject(project);
    navigate(teamId ? `/t/${teamId}/projects/${project.id}` : `/projects/${project.id}`);
  };

  const refreshProjects = async () => {
    try {
      const data = await fetchProjects();
      setProjects(data);
    } catch (err) {
      console.error('Failed to refresh projects:', err);
    }
  };

  // If reviewing a file
  if (reviewFile && selectedProject) {
    return (
      <VideoReviewPage
        projectId={selectedProject.id}
        file={reviewFile}
        onBack={() => {
          setReviewFile(null);
          navigate(teamId ? `/t/${teamId}/projects/${selectedProject.id}` : `/projects/${selectedProject.id}`);
        }}
        currentUserId={currentUserId || ''}
      />
    );
  }

  // If viewing a project (files or tasks)
  if (selectedProject) {
    return (
      <div className="max-w-7xl mx-auto animate-in fade-in slide-in-from-bottom-4 duration-500">
        {/* Tab bar */}
        <div className="flex items-center gap-1 mb-4 bg-zinc-900/50 border border-zinc-800 rounded-xl p-1 w-fit">
          <button
            onClick={() => setActiveTab('files')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
              activeTab === 'files'
                ? 'bg-zinc-800 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
            }`}
          >
            <FolderOpen size={16} />
            {t('sidebar.files')}
          </button>
          <button
            onClick={() => setActiveTab('tasks')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
              activeTab === 'tasks'
                ? 'bg-zinc-800 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
            }`}
          >
            <KanbanSquare size={16} />
            {t('topbar.tasks')}
          </button>
        </div>

        {activeTab === 'files' ? (
          <ProjectFilesView
            project={selectedProject}
            onBack={() => {
              setSelectedProject(null);
              navigate(teamId ? `/t/${teamId}/projects` : '/projects');
            }}
            onFileReview={(file) => {
              setReviewFile(file);
              navigate(teamId ? `/t/${teamId}/projects/${selectedProject.id}/review/${file.id}` : `/projects/${selectedProject.id}/review/${file.id}`);
            }}
          />
        ) : (
          <KanbanBoard
            projectId={selectedProject.id}
            teamId={selectedTeamId || undefined}
          />
        )}
      </div>
    );
  }

  // Projects list with sidebar
  return (
    <>
      <div className="flex h-full animate-in fade-in slide-in-from-bottom-4 duration-500">
        <ProjectsSidebar
          projects={projects}
          starredProjects={projects.filter(p => p.is_starred)}
          selectedProjectId={null}
          onProjectSelect={handleProjectSelect}
          onCreateProject={() => setIsCreateProjectModalOpen(true)}
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        />
        <div className="flex-1 min-w-0 px-6 py-4">
          <ProjectsListView
            projects={projects}
            onProjectSelect={handleProjectSelect}
            onCreateProject={() => setIsCreateProjectModalOpen(true)}
            onProjectsChange={refreshProjects}
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
          navigate(teamId ? `/t/${teamId}/projects/${project.id}` : `/projects/${project.id}`);
        }}
      />
    </>
  );
}
