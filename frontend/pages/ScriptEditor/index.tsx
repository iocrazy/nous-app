import { useParams } from 'react-router-dom';
import { ToastProvider } from '../../components/Toast';
import { ScriptEditorPage } from './ScriptEditorPage';

/**
 * Fullscreen route — mounted OUTSIDE AppLayout (see router.tsx),
 * which means AppLayout's <ToastProvider> doesn't reach into here.
 * AIChatPanel (and any other component this page mounts) calls
 * useToast() and would otherwise crash with
 * "useToast must be used within ToastProvider".
 *
 * Wrapping locally is the right scope: each fullscreen page has its
 * own toast queue, no cross-route bleed.
 */
export function ScriptEditor() {
  const { scriptId } = useParams<{ scriptId: string }>();
  void scriptId;
  return (
    <ToastProvider>
      <ScriptEditorPage />
    </ToastProvider>
  );
}
