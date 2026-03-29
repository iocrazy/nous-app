import { useParams } from 'react-router-dom';
import { CanvasEditorPage } from './CanvasEditorPage';

export function StoryboardWorkbench() {
  const { storyboardId } = useParams<{ storyboardId: string }>();
  // storyboardId is always present when this route is matched
  return <CanvasEditorPage />;
}
