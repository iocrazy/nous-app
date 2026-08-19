import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Check, Film, Loader2, RefreshCw } from 'lucide-react';
import { getSupabaseClient } from '../../supabaseClient';
import { extractCoverFrames, selectCoverFrame } from '../../services/distributionService';
import { getResourceFileUrl } from '../../services/resourceService';
import { CoverCandidate, CoverFramesMeta } from '../../types';
import { UiSelect } from '../ui/primitives';

/**
 * Cover picking, frame-first: sample N evenly-spaced frames out of a video the
 * user already chose, let them click one, and derive the vertical 3:4 +
 * horizontal 4:3 pair Douyin's creator page wants.
 *
 * Data flow (deliberately asymmetric, mirroring the backend):
 *
 *   extract  POST /covers/extract  → task_id, then Supabase Realtime.
 *            Downloading a multi-GB source and running N ffmpeg seeks is
 *            seconds-to-minutes work, so it is a DBOS workflow and the
 *            candidate list arrives in `task_tracking.metadata.cover_frames`.
 *   select   POST /covers/select   → both cover ids in the response.
 *            Re-reading one frame out of a source that is almost certainly
 *            still in the server's local media cache is fast; making the user
 *            wait on another Realtime round-trip would be a net loss.
 *
 * Candidates are NOT resources. Each one used to be persisted as its own
 * `derived` image row inheriting the source video's folder, so sampling a
 * video littered the user's Library with frames indistinguishable from real
 * material. Now the preview rides inline as a data URL and what gets sent back
 * on pick is the frame's `timestamp_seconds` — the server re-reads that exact
 * frame (same ffmpeg seek, proven to be deterministic) and crops it.
 *
 * Every terminal state is user-visible and says what to do next (spec §7.8:
 * a trigger path that fails silently is not acceptable). The three shapes that
 * matter are distinguishable and worded differently:
 *   - the request never landed (4xx/5xx from `extract`)
 *   - the workflow failed with a typed reason (`metadata.cover_frames.error`
 *     plus `error_status` — 504 means "retry may work", 4xx means "this video
 *     cannot be sampled")
 *   - the workflow went terminal without writing a reason (phase watch), or
 *     nothing arrived at all (watchdog)
 */

/** Task phases the trigger mirrors from DBOS. Anything in here means no
 *  further `metadata` update is coming for this task id. */
const TERMINAL_PHASES: ReadonlySet<string> = new Set([
  'failed', 'cancelled', 'lost',
]);

/**
 * Client-side upper bound on the wait (spec §7.2: every wait needs one).
 *
 * The server's own deadline is 600s for download + extraction; this sits just
 * past it so a workflow that dies without ever writing `metadata` — a worker
 * restart, a lost Realtime socket — surfaces as a retryable failure instead of
 * a spinner that spins forever.
 */
const SAMPLING_TIMEOUT_MS = 11 * 60 * 1000;

export interface CoverPair {
  vertical: string;
  horizontal: string;
}

export interface CoverPickerProps {
  /** The videos currently selected for this publish, in pick order. Empty
   *  means there is nothing to sample from — the picker says so rather than
   *  offering a button that cannot work. */
  sources: Array<{ id: string; filename: string }>;
  /** Derived covers, owned by the parent so they can ride along in the
   *  create-task payload. */
  value: CoverPair | null;
  onChange: (pair: CoverPair | null) => void;
}

type Status = 'idle' | 'starting' | 'sampling' | 'ready' | 'failed';

/** `12.5` → `0:12`. Nullable defensively: the value comes off a jsonb blob, and
 *  a tile whose timestamp is unusable must not claim a time it cannot honour —
 *  `onPickFrame` refuses the same shape rather than posting a bad coordinate. */
const formatStamp = (seconds: number | null | undefined): string | null => {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
};

export const CoverPicker: React.FC<CoverPickerProps> = ({
  sources, value, onChange,
}) => {
  const { t } = useTranslation();

  // Which selected video to sample. Defaults to the first and follows the
  // selection when the chosen one is removed.
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [meta, setMeta] = useState<CoverFramesMeta | null>(null);
  // Set when the workflow went terminal without leaving a reason in metadata.
  const [deadPhase, setDeadPhase] = useState<string | null>(null);
  const [startErrorStatus, setStartErrorStatus] = useState<number | null | undefined>(undefined);
  const [timedOut, setTimedOut] = useState(false);
  const [pickedFrame, setPickedFrame] = useState<number | null>(null);
  const [deriving, setDeriving] = useState(false);
  const [deriveError, setDeriveError] = useState<string | null>(null);
  // Signed URL transport for <img src> — headers are impossible there, and the
  // file endpoint accepts the Supabase JWT as ?token= (same as canvas output
  // nodes). Unsigned is the fallback, not the plan.
  const [token, setToken] = useState<string | undefined>(undefined);

  const sourceIds = useMemo(() => sources.map((s) => s.id), [sources]);
  const activeSource = sourceId && sourceIds.includes(sourceId) ? sourceId : sourceIds[0] ?? null;

  useEffect(() => {
    let alive = true;
    const supabase = getSupabaseClient();
    if (!supabase) return undefined;
    void supabase.auth.getSession()
      .then(({ data }) => { if (alive) setToken(data?.session?.access_token); })
      .catch((err) => console.error('distribution: read session for cover urls failed', err));
    return () => { alive = false; };
  }, []);

  /** Drop everything derived from a previous source/run. */
  const reset = useCallback(() => {
    setTaskId(null);
    setMeta(null);
    setDeadPhase(null);
    setStartErrorStatus(undefined);
    setTimedOut(false);
    setPickedFrame(null);
    setDeriveError(null);
  }, []);

  // Changing which video the covers come from invalidates the frames AND the
  // covers already derived from them — leaving the old pair in the payload
  // would publish a cover from a video the user just swapped out.
  const prevSource = useRef<string | null>(null);
  useEffect(() => {
    if (prevSource.current === activeSource) return;
    const had = prevSource.current !== null;
    prevSource.current = activeSource;
    if (!had) return;
    reset();
    onChange(null);
  // onChange is an inline arrow in the parent; keying on it would re-fire.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSource, reset]);

  // ── extract ──────────────────────────────────────────────────────────
  const start = useCallback(async () => {
    if (!activeSource) return;
    reset();
    setStartErrorStatus(null);
    try {
      const { task_id } = await extractCoverFrames({ resource_id: activeSource });
      setTaskId(task_id);
    } catch (err) {
      console.error('distribution: extract cover frames failed', err);
      // Keep the status: 404/422 mean this particular video cannot be sampled
      // (retrying is pointless), everything else is worth one more attempt.
      setStartErrorStatus((err as { status?: number } | null)?.status ?? 0);
    }
  }, [activeSource, reset]);

  // ── Realtime: task_tracking.metadata.cover_frames ────────────────────
  useEffect(() => {
    if (!taskId) return undefined;
    const supabase = getSupabaseClient();
    if (!supabase) {
      console.error('distribution: no Supabase client — cover frames cannot stream');
      return undefined;
    }

    const apply = (row: unknown) => {
      const r = row as {
        metadata?: { cover_frames?: CoverFramesMeta } | null;
        phase?: string | null;
      } | null;
      const frames = r?.metadata?.cover_frames;
      if (frames) setMeta(frames);
      // The trigger owns `phase`. A terminal phase with no `error` in metadata
      // is the one case the workflow cannot narrate for itself (killed worker,
      // cancelled task) — without watching it the UI would sample forever.
      const phase = r?.phase;
      if (phase && TERMINAL_PHASES.has(phase)) setDeadPhase(phase);
    };

    // `task_id` is the DBOS workflow id, which the endpoint reuses as
    // `task_tracking.dbos_workflow_id` — NOT the table's uuid PK, so filtering
    // on `id` matches nothing.
    const channel = supabase
      .channel(`cover-frames-${taskId}`)
      .on('postgres_changes', {
        event: 'UPDATE',
        schema: 'public',
        table: 'task_tracking',
        filter: `dbos_workflow_id=eq.${taskId}`,
      }, (payload) => apply(payload.new))
      .subscribe((chanStatus) => {
        if (chanStatus !== 'SUBSCRIBED') return;
        // Seed: a short video can finish before the websocket finishes
        // joining, and that UPDATE is then gone forever. Read the row once.
        void supabase
          .from('task_tracking')
          .select('metadata, phase')
          .eq('dbos_workflow_id', taskId)
          .maybeSingle()
          .then(({ data, error }) => {
            if (error) console.error('distribution: seed cover frames row failed', error);
            else apply(data);
          });
      });

    return () => { supabase.removeChannel(channel); };
  }, [taskId]);

  const candidates: CoverCandidate[] = meta?.candidates ?? [];
  const failure = meta?.error ?? null;

  const status: Status = (() => {
    if (startErrorStatus !== undefined && startErrorStatus !== null) return 'failed';
    if (failure || deadPhase || timedOut) return 'failed';
    if (candidates.length > 0) return 'ready';
    if (taskId) return 'sampling';
    if (startErrorStatus === null) return 'starting';
    return 'idle';
  })();

  // Watchdog — armed only while actually waiting.
  useEffect(() => {
    if (status !== 'sampling') return undefined;
    const timer = window.setTimeout(() => setTimedOut(true), SAMPLING_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [status, taskId]);

  // ── select ───────────────────────────────────────────────────────────
  const onPickFrame = async (candidate: CoverCandidate) => {
    if (deriving) return;
    // The source the frames were sampled from, as the workflow recorded it.
    // Preferred over `activeSource` because the user can change the dropdown
    // while a candidate strip from the previous video is still on screen; the
    // covers must come from the video these frames belong to.
    const from = meta?.source_resource_id ?? activeSource;
    const byCoordinate = Boolean(from) && Number.isFinite(candidate.timestamp_seconds);
    // Legacy candidates (see `CoverCandidate.resource_id`) carry an id instead
    // of a preview, and the backend that produced them only understands ids.
    const legacyId = candidate.preview_data_url ? undefined : candidate.resource_id;
    if (!byCoordinate && !legacyId) {
      // Never post a coordinate we cannot stand behind — a bad one would crop
      // the wrong frame or 400, and both look like "the button did nothing".
      console.error('distribution: candidate has no usable coordinate', candidate);
      setDeriveError(
        t('distribution.publish.coverFrameUnusable', 'That frame cannot be used — sample the video again.'),
      );
      return;
    }
    setDeriving(true);
    setDeriveError(null);
    setPickedFrame(candidate.index);
    try {
      const res = await selectCoverFrame(
        legacyId
          ? { frame_resource_id: legacyId }
          : {
            source_resource_id: from as string,
            timestamp_seconds: candidate.timestamp_seconds,
          },
      );
      onChange({
        vertical: res.cover_vertical_resource_id,
        horizontal: res.cover_horizontal_resource_id,
      });
    } catch (err) {
      console.error('distribution: derive cover pair failed', err);
      setPickedFrame(null);
      onChange(null);
      const status = (err as { status?: number } | null)?.status;
      setDeriveError(
        status === 422
          ? t('distribution.publish.coverCropUnusable', 'That frame could not be cropped — pick another one.')
          // 504 is specific to re-reading the frame: the source had to be
          // fetched again and that took too long. Saying "pick another frame"
          // there would send the user chasing a problem that is not the
          // frame's — the same attempt usually works once the fetch warms up.
          : status === 504
            ? t('distribution.publish.coverCropTimedOut', 'Reading that frame back took too long. Try the same frame again.')
            : t('distribution.publish.coverCropFailed', 'Could not build the covers from that frame. Try again, or pick another frame.'),
      );
    } finally {
      setDeriving(false);
    }
  };

  // ── failure copy: what happened, and what to do about it ─────────────
  const retryable = startErrorStatus === undefined || startErrorStatus === null
    ? true
    : !(startErrorStatus === 404 || startErrorStatus === 422);
  const failureText = (() => {
    if (startErrorStatus === 404) {
      return t('distribution.publish.coverSourceMissing', 'That video is no longer available — pick a different one.');
    }
    if (startErrorStatus === 422) {
      return t('distribution.publish.coverSourceUnusable', 'Frames can only be sampled from a video file. Pick a video, or set the cover on the platform.');
    }
    if (startErrorStatus >= 500) {
      // A 5xx is an assertion about US, not about the user's video. Saying
      // "that video is no longer available" here — which is what a bug once
      // made this component do for five straight attempts on a perfectly good
      // file — sends the user off to re-upload something that was never
      // broken. Name the side that actually failed.
      return t('distribution.publish.coverServerError', 'Something went wrong on our side while starting the sampling. Try again — if it keeps failing, the video itself is fine.');
    }
    if (startErrorStatus != null) {
      return t('distribution.publish.coverStartFailed', 'The request to sample frames did not go through. Check your connection and try again.');
    }
    if (failure) {
      // Server-authored detail first — it is the only place a specific reason
      // (unreadable codec, storage error) surfaces at all.
      const hint = meta?.error_status === 504
        ? t('distribution.publish.coverTimedOutHint', 'Sampling took too long — a shorter video, or another attempt, usually works.')
        : t('distribution.publish.coverFailedHint', 'This video could not be sampled.');
      return `${hint} ${failure}`;
    }
    if (timedOut) {
      return t('distribution.publish.coverNoResult', 'No frames arrived. The job may have been interrupted — try again.');
    }
    if (deadPhase === 'cancelled') {
      return t('distribution.publish.coverCancelled', 'Frame sampling was cancelled.');
    }
    return t('distribution.publish.coverFailedGeneric', 'Frame sampling stopped without producing frames. Try again.');
  })();

  const frameUrl = (id: string) => getResourceFileUrl(id, token);

  /**
   * One cover cell: the crop box, with its ratio caption UNDER it.
   *
   * The caption used to be an overlay pinned inside the filled box (and plain
   * text inside the empty one), which meant the two boxes were the only thing
   * carrying the layout — and since they are different heights (120 vs 96) the
   * shorter one was centred against the taller one, so the pair rendered
   * staggered rather than side by side. Wrapping each box in a cell and
   * captioning underneath is what the platform's own uploader does, and it
   * makes the two cells align on their top edge for free.
   */
  const slot = (kind: 'v' | 'h', resourceId: string | undefined, label: string) => (
    <div className="cover-cell">
      <div className={`cover-slot ${kind} ${resourceId ? 'filled' : ''}`}>
        {resourceId ? <img src={frameUrl(resourceId)} alt={label} /> : <Film />}
      </div>
      <span className="cover-cap">{label}</span>
    </div>
  );

  return (
    <>
      <div className="cover-slots">
        {slot('v', value?.vertical, t('distribution.publish.vertical34', 'Vertical 3:4'))}
        {slot('h', value?.horizontal, t('distribution.publish.horizontal43', 'Horizontal 4:3'))}
      </div>

      <div className="cover-frames">
        <div className="head">
          <b>
            <Film size={14} />
            {t('distribution.publish.coverFromVideo', 'Cover from a video frame')}
          </b>
          {status !== 'idle' && sources.length > 0 && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={status === 'starting' || status === 'sampling'}
              onClick={() => void start()}
            >
              <RefreshCw size={13} />
              {t('distribution.publish.coverResample', 'Sample again')}
            </button>
          )}
        </div>

        {sources.length === 0 ? (
          // No button here on purpose: an enabled control that cannot work is
          // worse than saying what is missing.
          <p className="cover-frames-hint">
            {t('distribution.publish.coverNeedsVideo', 'Pick a video above first — cover frames are sampled from it.')}
          </p>
        ) : (
          <>
            {sources.length > 1 && (
              <div className="cover-source">
                {/* No `htmlFor`: the control below is a button-based menu, not
                    a native <select>, so the accessible name rides on its own
                    `aria-label`. Pointing a label at the aria-hidden mirror
                    <select> would name a control screen readers cannot reach. */}
                <label>
                  {t('distribution.publish.coverSourceLabel', 'Sample from')}
                </label>
                {/* Same reason as the self-declaration field on the publish
                    page: a bare <select> paints with the OS widget instead of
                    the app's own menu. `triggerClassName` keeps this page's
                    `.input` field look. */}
                <UiSelect
                  triggerClassName="input"
                  aria-label={t('distribution.publish.coverSourceLabel', 'Sample from')}
                  value={activeSource ?? ''}
                  onChange={(e) => setSourceId(e.target.value)}
                >
                  {sources.map((s) => (
                    <option key={s.id} value={s.id}>{s.filename}</option>
                  ))}
                </UiSelect>
              </div>
            )}

            {status === 'idle' && (
              <>
                <button
                  type="button"
                  className="btn btn-solid btn-sm"
                  onClick={() => void start()}
                >
                  <Film size={13} />
                  {t('distribution.publish.coverPickFrame', 'Pick a frame from the video')}
                </button>
                <p className="cover-frames-hint">
                  {t('distribution.publish.coverPickFrameHint', 'Samples a few evenly spaced frames, then crops the one you choose to 3:4 and 4:3.')}
                </p>
              </>
            )}

            {(status === 'starting' || status === 'sampling') && (
              <p className="cover-frames-hint tone-info" role="status">
                <Loader2 size={13} className="spin" />
                {t('distribution.publish.coverSampling', 'Sampling frames — this can take a minute for a long video.')}
              </p>
            )}

            {status === 'failed' && (
              <div className="cover-frames-err">
                <p className="field-err" role="alert">
                  <AlertTriangle size={13} />
                  {failureText}
                </p>
                {retryable && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => void start()}
                  >
                    <RefreshCw size={13} />
                    {t('distribution.publish.coverRetry', 'Try again')}
                  </button>
                )}
              </div>
            )}

            {status === 'ready' && (
              <>
                <div
                  className="cover-cand-strip"
                  role="radiogroup"
                  aria-label={t('distribution.publish.coverCandidates', 'Candidate frames')}
                >
                  {candidates.map((c) => {
                    const on = pickedFrame === c.index;
                    const stamp = formatStamp(c.timestamp_seconds);
                    const label = stamp
                      ? t('distribution.publish.coverFrameAt', 'Frame at {{time}}', { time: stamp })
                      : t('distribution.publish.coverFrameNumbered', 'Frame {{n}}', { n: c.index + 1 });
                    return (
                      <button
                        key={c.index}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        aria-label={label}
                        title={label}
                        className={`cover-cand ${on ? 'sel' : ''}`}
                        disabled={deriving}
                        onClick={() => void onPickFrame(c)}
                      >
                        {/* The preview is inline base64 — no fetch, no token,
                            and nothing persisted to fetch it from. The
                            `frameUrl` arm only fires for legacy candidates
                            from a not-yet-shipped backend (see
                            `CoverCandidate.resource_id`); without it the strip
                            would render broken images for the few minutes the
                            frontend is ahead of the backend. */}
                        <img
                          src={c.preview_data_url ?? frameUrl(String(c.resource_id))}
                          alt=""
                        />
                        {stamp && <span className="stamp">{stamp}</span>}
                        {on && !deriving && (
                          <span className="pi-check"><Check size={12} strokeWidth={3} /></span>
                        )}
                        {on && deriving && (
                          <span className="pi-busy"><Loader2 size={14} className="spin" /></span>
                        )}
                      </button>
                    );
                  })}
                </div>
                <p className="cover-frames-hint">
                  {value
                    ? t('distribution.publish.coverReady', 'Both crops are ready — pick another frame to replace them.')
                    : t('distribution.publish.coverChoose', 'Choose the frame to use as the cover.')}
                </p>
              </>
            )}

            {deriveError && (
              <p className="field-err" role="alert">
                <AlertTriangle size={13} />
                {deriveError}
              </p>
            )}
          </>
        )}
      </div>
    </>
  );
};

export default CoverPicker;
