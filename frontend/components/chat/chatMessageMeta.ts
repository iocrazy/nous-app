/**
 * Readers for the bits of `AIChatMessage.metadata_json` the panel renders.
 * One place, so the Trajectory view and the bubbles agree on where the run id
 * lives (BIGINT snowflake, kept as a string on the wire).
 */
import type { AIChatMessage } from '../../types';

export function chatRunId(msg: AIChatMessage): string | null {
  const meta = msg.metadata_json;
  if (!meta || typeof meta !== 'object') return null;
  const raw = (meta as Record<string, unknown>).run_id;
  return typeof raw === 'string' && raw ? raw : null;
}
