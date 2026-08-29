/**
 * Result body for a `cover_frames` task: the sampled frames themselves.
 *
 * The previews ride in `task_tracking.metadata.cover_frames.candidates` as
 * small data URLs; before this body existed the task fell to the generic
 * view, which printed that metadata as JSON — a wall of base64 where the
 * user expected pictures.
 */
import React from 'react';

import type { UnifiedTask } from '../../../contexts/TaskManagerContext';
import type { CoverFramesMeta } from '../../../types';

function stamp(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '';
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

export function CoverFramesResultBody({ task }: { task: UnifiedTask }) {
  const meta = (task.metadata ?? {}) as { cover_frames?: CoverFramesMeta };
  const frames = meta.cover_frames;
  const candidates = frames?.candidates ?? [];

  if (frames?.error) {
    return (
      <div className="rounded-lg border border-danger-line bg-danger-soft p-3 text-xs text-content" data-testid="cover-frames-error">
        {frames.error}
      </div>
    );
  }
  if (candidates.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-ink-700 p-6 text-center text-xs text-ink-500">
        {task.status === 'completed' ? 'No frames attached' : 'Frames appear here when sampling finishes'}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-2" data-testid="cover-frames-grid">
        {candidates.map((c) => (
          <figure key={c.index} className="m-0">
            <img
              src={c.preview_data_url}
              alt={`Frame at ${stamp(c.timestamp_seconds)}`}
              className="w-full rounded-md object-cover"
              style={{ aspectRatio: `${c.preview_width || 16} / ${c.preview_height || 9}` }}
            />
            <figcaption className="mt-1 text-[11px] text-ink-400">{stamp(c.timestamp_seconds)}</figcaption>
          </figure>
        ))}
      </div>
      <div className="text-[11px] text-ink-500">
        {candidates.length} frames sampled evenly from the video — pick one in Cover Studio to crop it.
      </div>
    </div>
  );
}
