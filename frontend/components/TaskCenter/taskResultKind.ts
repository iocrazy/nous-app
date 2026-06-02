import type { UnifiedTask } from '../../contexts/TaskManagerContext';

export type ResultKind = 'media' | 'agent' | 'transcript' | 'summary' | 'generic';

/**
 * Which detail body renders a task's result. Media types need a produced
 * resource to preview; ai_extract (vision) has no read endpoint yet so it falls
 * through to generic (see the plan's Prerequisites).
 */
export function taskResultKind(
  task: Pick<UnifiedTask, 'task_type' | 'resource_id'>,
): ResultKind {
  switch (task.task_type) {
    case 'download':
    case 'parse':
    case 'upload':
    case 'transcode':
      return task.resource_id ? 'media' : 'generic';
    case 'agent':
      return 'agent';
    case 'ai_transcription':
      return task.resource_id ? 'transcript' : 'generic';
    case 'ai_summary':
      return task.resource_id ? 'summary' : 'generic';
    default:
      return 'generic';
  }
}
