// components/Distribution/CoverStudio/CoverFrameGrabber.tsx
//
// The "Grab a frame" card: play the selected video, stop on the face you want,
// press grab. The frame lands as a generated_media row (via
// POST /distribution/covers/grab-frame) and the parent adds it to the pool.
//
// A native <video> on purpose, not components/VideoPlayer.tsx: that player is
// built for the HLS review surfaces (quality menu, fullscreen, shortcut layer,
// comment markers on its own timeline). The grabber needs exactly three things
// — seek, current time, and dots for frames already taken — and inheriting the
// player would mean inheriting its HLS bootstrapping in every test.
//
// ★ What is grabbed is a TIMESTAMP, not the <video> pixels. The server re-reads
// the frame from the source file at that second (`seek_frame_cmd`, the same
// builder the cover-frame flow pins), so the grabbed image is full quality —
// not a screenshot of a possibly-downscaled stream. "Same second = same frame"
// is measured, not assumed (byte-identical across containers/codecs/VFR).

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Camera } from 'lucide-react';

import { getSupabaseClient } from '../../../supabaseClient';
import { getResourceFileUrl } from '../../../services/resourceService';
import { grabCoverFrame } from '../../../services/distributionService';
import type { LibraryVideo } from '../../../types';
import { formatTimestamp } from './coverReferences';
import './cover-studio.css';

export interface GrabbedFrame {
  generatedMediaId: string;
  url: string;
  timestampSeconds: number;
}

interface Props {
  /** The videos picked on the publish page — same list CoverPicker gets. */
  sources: LibraryVideo[];
  /** Timestamps already in the pool, drawn as dots on the track. */
  grabbedAt: number[];
  onGrabbed: (frame: GrabbedFrame) => void;
  /** True when the pool cannot take another frame (9/9). */
  poolFull?: boolean;
}

export function CoverFrameGrabber({
  sources,
  grabbedAt,
  onGrabbed,
  poolFull = false,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [token, setToken] = useState<string | undefined>(undefined);
  const [duration, setDuration] = useState(0);
  const [current, setCurrent] = useState(0);
  const [grabbing, setGrabbing] = useState(false);
  const [grabError, setGrabError] = useState<string | null>(null);

  const active = sources.find((s) => s.id === sourceId) ?? sources[0] ?? null;

  // <video src> cannot carry headers; the file endpoint accepts the Supabase
  // JWT as ?token= — same transport CoverPicker already uses for its slots.
  useEffect(() => {
    let alive = true;
    const supabase = getSupabaseClient();
    if (!supabase) return undefined;
    void supabase.auth
      .getSession()
      .then(({ data }) => {
        if (alive) setToken(data?.session?.access_token);
      })
      .catch((err) =>
        console.error('[CoverFrameGrabber] read session failed:', err),
      );
    return () => {
      alive = false;
    };
  }, []);

  // Switching videos resets the clock — keeping the old position would draw
  // the playhead (and the grab caption) at a second that belongs to another
  // file.
  useEffect(() => {
    setCurrent(0);
    setDuration(0);
    setGrabError(null);
  }, [active?.id]);

  const grab = useCallback(async () => {
    if (!active || grabbing || poolFull) return;
    // Pause first: the grabbed timestamp must be the second the user is
    // LOOKING at. On a playing video the clock advances between the click and
    // the read, and the server would re-read a frame the user never saw.
    videoRef.current?.pause();
    const at = videoRef.current?.currentTime ?? current;
    setGrabbing(true);
    setGrabError(null);
    try {
      const res = await grabCoverFrame(active.id, at);
      onGrabbed({
        generatedMediaId: res.generated_media_id,
        url: res.url,
        timestampSeconds: res.timestamp_seconds,
      });
    } catch (err) {
      console.error('[CoverFrameGrabber] grab failed:', err);
      // The server's sentence when there is one (422 "could not re-read the
      // frame…", 504 timeout) — it is the only place a specific reason exists.
      const detail = (err as { detail?: unknown })?.detail;
      setGrabError(
        typeof detail === 'string'
          ? detail
          : t(
              'distribution.coverStudio.grabFailed',
              'That frame could not be grabbed. Try another moment.',
            ),
      );
    } finally {
      setGrabbing(false);
    }
  }, [active, grabbing, poolFull, current, onGrabbed, t]);

  if (!active) {
    // No button here on purpose: an enabled control that cannot work is worse
    // than saying what is missing (CoverPicker's own rule).
    return (
      <div className="cs-card">
        <h4>{t('distribution.coverStudio.grabFrame', 'Grab a frame')}</h4>
        <div className="cs-empty">
          {t(
            'distribution.coverStudio.grabNeedsVideo',
            'Pick a video on the publish page first — frames are grabbed from it.',
          )}
        </div>
      </div>
    );
  }

  const src = getResourceFileUrl(active.id, token);
  const progress = duration > 0 ? (current / duration) * 100 : 0;

  return (
    <div className="cs-card">
      <h4>
        {t('distribution.coverStudio.grabFrame', 'Grab a frame')}
        <span className="aux">{formatTimestamp(current)}</span>
      </h4>
      <div className="cs-body">
        {sources.length > 1 && (
          <select
            className="cs-source"
            aria-label={t('distribution.coverStudio.grabSource', 'Grab from')}
            value={active.id}
            onChange={(e) => setSourceId(e.target.value)}
          >
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.filename}
              </option>
            ))}
          </select>
        )}

        <div className="cs-stage">
          {/* crossOrigin so a future canvas use never taints; preload metadata
              so duration arrives without pulling the whole file. */}
          <video
            ref={videoRef}
            src={src}
            preload="metadata"
            controls={false}
            data-testid="cover-grab-video"
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
            onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime || 0)}
            onClick={() => {
              const v = videoRef.current;
              if (!v) return;
              if (v.paused) void v.play();
              else v.pause();
            }}
          />
        </div>

        <div
          className="cs-track"
          data-testid="cover-grab-track"
          role="slider"
          aria-label={t('distribution.coverStudio.grabTimeline', 'Timeline')}
          aria-valuemin={0}
          aria-valuemax={duration}
          aria-valuenow={current}
        >
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.1}
            value={current}
            onChange={(e) => {
              const v = videoRef.current;
              const at = Number(e.target.value);
              if (v) v.currentTime = at;
              setCurrent(at);
            }}
          />
          <span className="played" style={{ width: `${progress}%` }} />
          {/* Dots are frames you already took — the design's own caption. */}
          {duration > 0 &&
            grabbedAt.map((at, i) => (
              <span
                key={`${at}-${i}`}
                className="mark"
                data-testid="cover-grab-mark"
                style={{ left: `${Math.min(100, (at / duration) * 100)}%` }}
              />
            ))}
        </div>

        <button
          type="button"
          className="cs-grab"
          disabled={grabbing || poolFull}
          onClick={() => void grab()}
          data-testid="cover-grab-button"
        >
          <Camera size={14} />
          {grabbing
            ? t('distribution.coverStudio.grabbing', 'Grabbing…')
            : poolFull
              ? t('distribution.coverStudio.grabPoolFull', 'Pool is full (9/9)')
              : t('distribution.coverStudio.grabThis', 'Grab this frame')}
        </button>

        {grabError && (
          <div className="cs-error" style={{ margin: '10px 0 0' }} data-testid="cover-grab-error">
            {grabError}
          </div>
        )}

        <p className="cs-hint" style={{ marginTop: 9 }}>
          {t(
            'distribution.coverStudio.grabDots',
            'Dots are frames you already took. The frame is re-read from the source file at full quality — not a screenshot of the player.',
          )}
        </p>
      </div>
    </div>
  );
}
