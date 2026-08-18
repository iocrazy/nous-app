import { useContext } from 'react';
import {
  TaskManagerContext,
  type TaskManagerContextType,
} from '../contexts/TaskManagerContext';

/**
 * Like `useTaskManager`, but returns null outside a TaskManagerProvider
 * instead of throwing — same shape as `useOptionalToast`.
 *
 * The floating AI chat mounts in two hosts: AppLayout (inside the
 * provider) and the fullscreen Script / Storyboard editor routes (outside
 * it). Task progress in the chat is a nice-to-have there, so the chat
 * degrades to "no progress dot" rather than crashing the editor.
 *
 * NOTE for tests: a `vi.mock('contexts/TaskManagerContext')` factory must
 * export `TaskManagerContext` too if the suite renders such a component.
 */
export function useOptionalTaskManager(): TaskManagerContextType | null {
  return useContext(TaskManagerContext);
}
