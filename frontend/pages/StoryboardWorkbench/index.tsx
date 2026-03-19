import { useParams } from 'react-router-dom';
import { ProjectListPage } from './ProjectListPage';
import { CanvasEditorPage } from './CanvasEditorPage';

export function StoryboardWorkbench() {
  const { projectId } = useParams<{ projectId?: string }>();

  if (!projectId) {
    return <ProjectListPage />;
  }

  return <CanvasEditorPage />;
}
