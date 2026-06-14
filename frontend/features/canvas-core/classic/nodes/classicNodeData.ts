/**
 * Run-lifecycle fields shared by every ClassicMode node's `data` blob
 * (Phase 5a B4). Mirrors the SmartMode run vocabulary so the cascade
 * runner (B5) can drive both modes the same way.
 */

import type { ClassicRunStatus } from './ClassicNodeShell';

export interface ClassicRunData {
  run_status: ClassicRunStatus;
  run_started_at: string | null;
  run_error: string | null;
}

/** Read the run fields off an untyped node `data` blob with safe defaults. */
export function readRunData(data: unknown): ClassicRunData {
  const obj = (data ?? {}) as Record<string, unknown>;
  const status = obj.run_status;
  return {
    run_status: isRunStatus(status) ? status : 'idle',
    run_started_at:
      typeof obj.run_started_at === 'string' ? obj.run_started_at : null,
    run_error: typeof obj.run_error === 'string' ? obj.run_error : null,
  };
}

const RUN_STATUSES: ReadonlyArray<ClassicRunStatus> = [
  'idle',
  'queued',
  'running',
  'succeeded',
  'failed',
  'blocked',
];

function isRunStatus(value: unknown): value is ClassicRunStatus {
  return (
    typeof value === 'string' &&
    (RUN_STATUSES as ReadonlyArray<string>).includes(value)
  );
}
