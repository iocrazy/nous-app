import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Project, ProjectFile } from '../types';
import { useAuth } from '../contexts/AuthContext';
import { ProjectsListView } from '../components/ProjectsListView';
import { ProjectFilesView } from '../components/ProjectFilesView';
import { VideoReviewPage } from '../components/VideoReviewPage';
import { CreateProjectModal } from '../components/CreateProjectModal';

export function ProjectsPage() {
  const navigate = useNavigate();
  const { projectId, fileId } = useParams();
  const { currentUserId } = useAuth();

  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [reviewFile, setReviewFile] = useState<ProjectFile | null>(null);
  const [isCreateProjectModalOpen, setIsCreateProjectModalOpen] = useState(false);

  // If reviewing a file
  if (reviewFile && selectedProject) {
    return (
      <VideoReviewPage
        projectId={selectedProject.id}
        file={reviewFile}
        onBack={() => {
          setReviewFile(null);
          navigate(`/projects/${selectedProject.id}`);
        }}
        currentUserId={currentUserId || ''}
      />
    );
  }

  // If viewing project files
  if (selectedProject) {
    return (
      <div className="max-w-7xl mx-auto animate-in fade-in slide-in-from-bottom-4 duration-500">
        <ProjectFilesView
          project={selectedProject}
          onBack={() => {
            setSelectedProject(null);
            navigate('/projects');
          }}
          onFileReview={(file) => {
            setReviewFile(file);
            navigate(`/projects/${selectedProject.id}/review/${file.id}`);
          }}
        />
      </div>
    );
  }

  // Projects list
  return (
    <>
      <div className="max-w-7xl mx-auto animate-in fade-in slide-in-from-bottom-4 duration-500">
        <ProjectsListView
          onProjectSelect={(project) => {
            setSelectedProject(project);
            navigate(`/projects/${project.id}`);
          }}
          onCreateProject={() => setIsCreateProjectModalOpen(true)}
        />
      </div>
      <CreateProjectModal
        isOpen={isCreateProjectModalOpen}
        onClose={() => setIsCreateProjectModalOpen(false)}
        onProjectCreated={(project) => {
          setIsCreateProjectModalOpen(false);
          setSelectedProject(project);
          navigate(`/projects/${project.id}`);
        }}
      />
    </>
  );
}
