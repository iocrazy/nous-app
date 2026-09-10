/**
 * Copy for a refused wake-up (harness 2b-2 §5-2). `schedulesService` throws a
 * typed ScheduleRejectedError; this maps its `code` to a sentence the user can
 * act on — same shape as forkErrors.ts / issueControlErrors.ts.
 *
 * The codes are the ones `backend/app/api/schedules_router.py` actually
 * raises through `_bad_request`, not invented ones. An unknown code still
 * names itself, so a person can quote it in a bug report; the raw response
 * body never appears (`reject()` already stripped it).
 */
import { ScheduleRejectedError } from '../../services/schedulesService';

type T = (key: string, fallback: string, vars?: Record<string, unknown>) => string;

const COPY: Record<string, [key: string, fallback: string]> = {
  fire_at_out_of_range: ['later.error.fire_at_out_of_range', 'Pick a time in the future, within 30 days'],
  fire_at_timezone_required: ['later.error.fire_at_timezone_required', 'That time carried no time zone — pick it again'],
  fire_at_required: ['later.error.fire_at_required', 'Pick a time for this wake-up'],
  text_required: ['later.error.text_required', 'Write the message this wake-up should send'],
  issue_id_required: ['later.error.issue_id_required', 'This issue could not be identified'],
  invalid_timezone: ['later.error.invalid_timezone', 'That time zone is not one the server knows'],
  http_403: ['later.error.forbidden', 'You cannot schedule anything on this issue'],
  http_404: ['later.error.not_found', 'Issue not found'],
};

export function laterErrorText(err: unknown, t: T): string {
  if (err instanceof ScheduleRejectedError) {
    const hit = COPY[err.code];
    if (hit) return t(hit[0], hit[1]);
    // Unknown code: name it rather than printing the server's prose, which
    // may be an internal sentence, and never the envelope.
    return t('later.error.unknown', 'Could not schedule that wake-up ({{code}})', { code: err.code });
  }
  return t('later.error.generic', 'Could not schedule that wake-up.');
}
