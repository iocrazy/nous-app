import type { UnifiedTask } from '../../contexts/TaskManagerContext';

export type ResultKind = 'media' | 'agent' | 'transcript' | 'summary' | 'vision' | 'canvasGen' | 'coverFrames' | 'generic';

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
    case 'canvas_gen':
    case 'canvas_timeline':
    // Cover Studio runs the same generation workflow; its result_url is the
    // 2x2 drafts grid or the final cover, previewed the same way.
    case 'cover_gen':
      // Result lives in task metadata (durable result_url), no resource row.
      return 'canvasGen';
    case 'cover_frames':
      // The sampled frames ride in metadata.cover_frames as small data URLs.
      return 'coverFrames';
    case 'ai_transcription':
      return task.resource_id ? 'transcript' : 'generic';
    case 'ai_summary':
      return task.resource_id ? 'summary' : 'generic';
    case 'ai_extract':
      return task.resource_id ? 'vision' : 'generic';
    default:
      return 'generic';
  }
}
