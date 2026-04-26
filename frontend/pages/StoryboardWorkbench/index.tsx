import { useParams } from 'react-router-dom';
import { ToastProvider } from '../../components/Toast';
import { CanvasEditorPage } from './CanvasEditorPage';

/**
 * Fullscreen route — mounted OUTSIDE AppLayout (see router.tsx).
 * Mirrors ScriptEditor: scope a local ToastProvider so any component
 * inside (current or future) can call useToast() without crashing.
 */
export function StoryboardWorkbench() {
  const { storyboardId } = useParams<{ storyboardId: string }>();
  void storyboardId;
  return (
    <ToastProvider>
      <CanvasEditorPage />
    </ToastProvider>
  );
}
