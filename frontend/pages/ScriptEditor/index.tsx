import { useParams } from 'react-router-dom';
import { ToastProvider } from '../../components/Toast';
import { TaskManagerProvider } from '../../contexts/TaskManagerContext';
import { useAuth } from '../../contexts/AuthContext';
import { EditorShell } from '../../editor/components/EditorShell';
import { FloatingChatWidget } from '../../components/FloatingChatWidget';

/**
 * Fullscreen route — mounted OUTSIDE AppLayout (see router.tsx),
 * which means AppLayout's <ToastProvider> doesn't reach into here.
 * AIChatPanel (and any other component this page mounts) calls
 * useToast() and would otherwise crash with
 * "useToast must be used within ToastProvider".
 *
 * Wrapping locally is the right scope: each fullscreen page has its
 * own toast queue, no cross-route bleed.
 *
 * TaskManagerProvider is needed for the same reason: ImportScriptModal →
 * useTaskCompletion → useTaskManager() would otherwise crash this route
 * with "useTaskManager must be used within TaskManagerProvider".
 * It only depends on AuthContext, which is provided at the root.
 *
 * FloatingChatWidget is the global AI chat's second host: this fullscreen
 * route lives OUTSIDE AppLayout (which mounts the widget for normal routes),
 * so without this mount the workspace chat — and the "select text → AI chat"
 * flow that drives it — would be unavailable on the script editor. Only one
 * host renders at a time (globalChatStore), so this never doubles the FAB.
 */
export function ScriptEditor() {
  const { scriptId, projectId } = useParams<{ scriptId: string; projectId: string }>();
  const { currentUserId, userProfile } = useAuth();
  if (!scriptId) return null;
  return (
    <ToastProvider>
      <TaskManagerProvider>
        <EditorShell
          scriptId={scriptId}
          currentUserId={currentUserId}
          currentUserName={userProfile.name}
          projectId={projectId}
        />
        <FloatingChatWidget />
      </TaskManagerProvider>
    </ToastProvider>
  );
}
