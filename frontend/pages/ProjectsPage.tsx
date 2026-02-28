import { useState, useEffect } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { FolderOpen, KanbanSquare, Share2, Trash2 } from 'lucide-react';
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
import { ProjectTrashView } from '../components/ProjectTrashView';
import { ProjectSharesView } from '../components/ProjectSharesView';

type ProjectTab = 'files' | 'tasks' | 'shares' | 'trash';

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
  const [shareCount, setShareCount] = useState(0);
  const [trashCount, setTrashCount] = useState(0);

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
    navigate(teamId ? `/team/${teamId}/projects/${project.id}` : `/projects/${project.id}`);
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
          navigate(teamId ? `/team/${teamId}/projects/${selectedProject.id}` : `/projects/${selectedProject.id}`);
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
        <div className="flex items-center gap-4 mb-4 border-b border-zinc-800">
          {([
            { tab: 'files' as ProjectTab, icon: <FolderOpen size={15} />, label: t('projects.tabs.files', 'Files') },
            { tab: 'tasks' as ProjectTab, icon: <KanbanSquare size={15} />, label: t('projects.tabs.tasks', 'Tasks') },
            { tab: 'shares' as ProjectTab, icon: <Share2 size={15} />, label: t('projects.tabs.shares', 'Shares'), count: shareCount },
            { tab: 'trash' as ProjectTab, icon: <Trash2 size={15} />, label: t('projects.tabs.trash', 'Trash'), count: trashCount },
          ]).map(({ tab, icon, label, count }) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex items-center gap-1.5 pb-2.5 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab
                  ? 'border-indigo-500 text-white'
                  : 'border-transparent text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {icon}
              {label}
              {count != null && count > 0 && (
                <span className="text-[10px] bg-zinc-700 text-zinc-300 px-1.5 py-0.5 rounded-full">
                  {count}
                </span>
              )}
            </button>
          ))}
        </div>

        {activeTab === 'files' && (
          <ProjectFilesView
            project={selectedProject}
            onBack={() => {
              setSelectedProject(null);
              navigate(teamId ? `/team/${teamId}/projects` : '/projects');
            }}
            onFileReview={(file) => {
              setReviewFile(file);
              navigate(teamId ? `/team/${teamId}/projects/${selectedProject.id}/review/${file.id}` : `/projects/${selectedProject.id}/review/${file.id}`);
            }}
          />
        )}
        {activeTab === 'tasks' && (
          <KanbanBoard
            projectId={selectedProject.id}
            teamId={selectedTeamId || undefined}
          />
        )}
        {activeTab === 'shares' && (
          <ProjectSharesView
            projectId={selectedProject.id}
            onCountChange={setShareCount}
          />
        )}
        {activeTab === 'trash' && (
          <ProjectTrashView
            projectId={selectedProject.id}
            onCountChange={setTrashCount}
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
          navigate(teamId ? `/team/${teamId}/projects/${project.id}` : `/projects/${project.id}`);
        }}
      />
    </>
  );
}
