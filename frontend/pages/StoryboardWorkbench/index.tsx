import React from 'react';
import { useStoryboardStore } from '../../stores/storyboardStore';
import { ProjectListPage } from './ProjectListPage';
import { CanvasEditorPage } from './CanvasEditorPage';

export function StoryboardWorkbench() {
  const currentProjectId = useStoryboardStore((s) => s.currentProjectId);

  if (currentProjectId === null) {
    return <ProjectListPage />;
  }

  return <CanvasEditorPage />;
}
