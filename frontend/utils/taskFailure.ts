// Classify a failed task by its error_msg so the UI can (a) show a human
// label instead of a raw exception / base64 pickle, and (b) hide the Retry
// affordance when retrying cannot possibly help.
//
// "permanent" = the source itself is unavailable (track has no playable URL,
// 404). Re-running the same workflow yields the identical failure, so Retry
// is misleading. "transient" = network/timeout — a retry may succeed.
//
// Unknown errors stay retryable and keep their raw text (truncated by the
// row), so we never hide a recoverable failure behind a guess.

export interface FailureClass {
  /** Retrying re-runs the same input and will fail identically. Hide Retry. */
  permanent: boolean;
  /** Human-readable label, or null to fall back to the raw error_msg. */
  friendly: string | null;
}

const NONE: FailureClass = { permanent: false, friendly: null };

export function classifyFailure(errorMsg?: string | null): FailureClass {
  if (!errorMsg) return NONE;
  const e = errorMsg;

  // Permanent — source unavailable; same input will fail again.
  if (/no url_player_info/i.test(e)) return { permanent: true, friendly: 'Track unavailable' };
  if (/SodaApiError/i.test(e)) return { permanent: true, friendly: 'Track unavailable' };
  if (/\b404\b|not\s*found|does not exist|been removed|deleted/i.test(e)) {
    return { permanent: true, friendly: 'Source not found' };
  }

  // Transient — network / timeout; a retry may succeed.
  if (/TimeoutError|timed out|timeout/i.test(e)) return { permanent: false, friendly: 'Timed out' };
  if (/dns resolution|URLBlocked/i.test(e)) return { permanent: false, friendly: 'Network error' };
  if (/ReadError|ConnectError|Connection|ECONN|network/i.test(e)) {
    return { permanent: false, friendly: 'Network error' };
  }

  // Unknown — keep raw text, stay retryable.
  return NONE;
}

/** Label to show in a row: the friendly label when known, else the raw text. */
export function failureLabel(errorMsg?: string | null): string | null {
  if (!errorMsg) return null;
  return classifyFailure(errorMsg).friendly ?? errorMsg;
}
