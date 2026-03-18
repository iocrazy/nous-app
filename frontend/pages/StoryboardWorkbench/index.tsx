import React, { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useStoryboardStore } from '../../stores/storyboardStore';
import { ProjectListPage } from './ProjectListPage';
import { CanvasEditorPage } from './CanvasEditorPage';

export function StoryboardWorkbench() {
  const { projectId } = useParams<{ projectId?: string }>();
  const currentProjectId = useStoryboardStore((s) => s.currentProjectId);
  const setCurrentProject = useStoryboardStore((s) => s.setCurrentProject);

  // Sync URL param → store
  useEffect(() => {
    if (projectId && projectId !== currentProjectId) {
      setCurrentProject(projectId);
    } else if (!projectId && currentProjectId) {
      setCurrentProject(null);
    }
  }, [projectId, currentProjectId, setCurrentProject]);

  if (!projectId) {
    return <ProjectListPage />;
  }

  return <CanvasEditorPage />;
}
