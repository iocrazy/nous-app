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
import { Camera, Check, Upload, User, ZoomIn, ZoomOut } from 'lucide-react';

import { getSupabaseClient } from '../../../supabaseClient';
import { getResourceFileUrl } from '../../../services/resourceService';
import { grabCoverFrame, listLibraryMediaOrThrow } from '../../../services/distributionService';
import type { LibraryVideo } from '../../../types';
import { formatTimestamp } from './coverReferences';
import { HelpTip } from './HelpTip';
import { useCoverFrameCandidates } from './useCoverFrameCandidates';
import './cover-studio.css';

/** Normalised crop anchor — the centre of the crop box over the frame. */
export interface CropFocus {
  x: number;
  y: number;
}

const CENTRE: CropFocus = { x: 0.5, y: 0.5 };
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const KEY_STEP = 0.05;

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
  /** `asPerson` — the frame was taken with "Set as person", not "Grab". */
  onGrabbed: (frame: GrabbedFrame, asPerson: boolean) => void;
  /** True when the pool cannot take another frame (9/9). */
  poolFull?: boolean;
  /**
   * Stage mode (the v4 layout): no card chrome, a crop guide drawn over the
   * video in the cover's aspect, and two more ways out of the frame —
   * "use this frame as the cover" (no AI) and "upload a picture instead".
   */
  embedded?: boolean;
  /** Aspect of the cover being set; drives the crop guide. */
  aspect?: '3:4' | '4:3';
  /** No-AI path: the server crops this exact frame into the cover, anchored
   *  where the user dragged the box. */
  onUseAsCover?: (
    sourceId: string,
    timestampSeconds: number,
    focus: CropFocus,
    zoom: number,
  ) => Promise<void>;
  /** A picture from disk becomes the cover as-is. */
  onUploadCover?: (file: File) => Promise<void>;
  /** Lets the stage offer the library's videos when the publish page has
   *  none selected yet — the studio can be opened before anything else. */
  scopeId?: string;
  /** Fired when a video is picked here, so the parent can select it too. */
  onPickSource?: (video: LibraryVideo) => void;
}

export function CoverFrameGrabber({
  sources,
  grabbedAt,
  onGrabbed,
  poolFull = false,
  embedded = false,
  aspect = '3:4',
  onUseAsCover,
  onUploadCover,
  scopeId,
  onPickSource,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [token, setToken] = useState<string | undefined>(undefined);
  const [duration, setDuration] = useState(0);
  const [current, setCurrent] = useState(0);
  const [grabbing, setGrabbing] = useState(false);
  const [grabError, setGrabError] = useState<string | null>(null);
  const [using, setUsing] = useState(false);
  const uploadRef = useRef<HTMLInputElement>(null);
  // Where the crop box is anchored, normalised to the VIDEO picture (not the
  // stage box — the video is letterboxed inside it). Centre until dragged.
  const [focus, setFocus] = useState<CropFocus>(CENTRE);
  // Zoom, the platform's way: the crop box stays put and the PICTURE grows
  // and can be dragged behind it. 1 = the box is as large as the picture
  // allows; 2 = the picture is twice as big inside the same box.
  const [zoom, setZoom] = useState(1);
  const [videoSize, setVideoSize] = useState<{ w: number; h: number } | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ startX: number; startY: number; focus: CropFocus } | null>(null);

  // A video picked inside the studio, for when the publish page had none.
  const [picked, setPicked] = useState<LibraryVideo | null>(null);
  const [library, setLibrary] = useState<LibraryVideo[] | null>(null);
  const [libraryError, setLibraryError] = useState(false);
  const [choosing, setChoosing] = useState(false);

  const active =
    sources.find((s) => s.id === sourceId) ?? sources[0] ?? picked ?? null;
  // The filmstrip: evenly spaced previews from the server (the old publish-page
  // picker's sampling, ported). Click one to seek; the strip is also the track.
  const strip = useCoverFrameCandidates(active?.id ?? null);

  const loadLibrary = useCallback(async () => {
    if (!scopeId) return;
    setLibraryError(false);
    try {
      const rows = await listLibraryMediaOrThrow(scopeId, { mediaType: 'video' });
      setLibrary(rows);
    } catch (err) {
      console.error('[CoverFrameGrabber] library videos failed:', err);
      setLibraryError(true);
      setLibrary([]);
    }
  }, [scopeId]);

  useEffect(() => {
    if (active || !scopeId || library !== null) return;
    void loadLibrary();
  }, [active, scopeId, library, loadLibrary]);

  const pickVideo = (v: LibraryVideo) => {
    setPicked(v);
    setChoosing(false);
    onPickSource?.(v);
  };

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
    setFocus(CENTRE);
    setZoom(1);
    setVideoSize(null);
  }, [active?.id]);

  /** The rectangle the video picture actually occupies inside the stage
   *  (object-fit: contain), in stage-local pixels. */
  const pictureRect = useCallback(() => {
    const stage = stageRef.current;
    if (!stage || !videoSize || videoSize.w === 0 || videoSize.h === 0) return null;
    const sw = stage.clientWidth;
    const sh = stage.clientHeight;
    if (sw === 0 || sh === 0) return null;
    const scale = Math.min(sw / videoSize.w, sh / videoSize.h);
    const w = videoSize.w * scale;
    const h = videoSize.h * scale;
    return { left: (sw - w) / 2, top: (sh - h) / 2, width: w, height: h };
  }, [videoSize]);

  /** The crop box: the largest `aspect` box the picture allows, fixed in the
   *  middle of the stage. Zoom and focus move the PICTURE under it. */
  const boxRect = () => {
    const pic = pictureRect();
    if (!pic) return null;
    const target = aspect === '4:3' ? 4 / 3 : 3 / 4;
    const picAspect = pic.width / pic.height;
    const w = picAspect > target ? pic.height * target : pic.width;
    const h = picAspect > target ? pic.height : pic.width / target;
    return { left: pic.left + (pic.width - w) / 2, top: pic.top + (pic.height - h) / 2, width: w, height: h };
  };
  const guideStyle = (): React.CSSProperties | undefined => boxRect() ?? undefined;

  /** Where the window the server will crop sits, in picture-normalised
   *  coordinates — the same maths as `center_crop_region(focus, zoom)`. */
  const cropWindow = () => {
    const pic = pictureRect();
    const box = boxRect();
    if (!pic || !box) return null;
    const w = box.width / pic.width / zoom;
    const h = box.height / pic.height / zoom;
    const x = Math.min(Math.max(focus.x - w / 2, 0), 1 - w);
    const y = Math.min(Math.max(focus.y - h / 2, 0), 1 - h);
    return { x, y, w, h };
  };

  /** Scale + shift the video so the crop window fills the fixed box. */
  const videoStyle = (): React.CSSProperties | undefined => {
    const pic = pictureRect();
    const box = boxRect();
    const win = cropWindow();
    if (!pic || !box || !win) return undefined;
    // The window's centre, in stage pixels at zoom 1 …
    const cx = pic.left + (win.x + win.w / 2) * pic.width;
    const cy = pic.top + (win.y + win.h / 2) * pic.height;
    // … must land on the box centre after scaling about the stage centre.
    const stage = stageRef.current;
    const sx = (stage?.clientWidth ?? 0) / 2;
    const sy = (stage?.clientHeight ?? 0) / 2;
    const bx = box.left + box.width / 2;
    const by = box.top + box.height / 2;
    const tx = bx - (sx + (cx - sx) * zoom);
    const ty = by - (sy + (cy - sy) * zoom);
    return { transform: `translate(${tx}px, ${ty}px) scale(${zoom})`, transformOrigin: 'center center' };
  };

  const moveFocus = useCallback((dx: number, dy: number) => {
    setFocus((f) => ({ x: clamp01(f.x + dx), y: clamp01(f.y + dy) }));
  }, []);

  const onGuidePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { startX: e.clientX, startY: e.clientY, focus };
  };
  const onGuidePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    const pic = pictureRect();
    if (!d || !pic) return;
    // Dragging moves the PICTURE (the box stays): drag right → picture goes
    // right → the window's centre moves left in picture coordinates.
    setFocus({
      x: clamp01(d.focus.x - (e.clientX - d.startX) / (pic.width * zoom)),
      y: clamp01(d.focus.y - (e.clientY - d.startY) / (pic.height * zoom)),
    });
  };
  const onGuidePointerUp = () => {
    drag.current = null;
  };
  const onGuideKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const map: Record<string, [number, number]> = {
      ArrowLeft: [-KEY_STEP, 0],
      ArrowRight: [KEY_STEP, 0],
      ArrowUp: [0, -KEY_STEP],
      ArrowDown: [0, KEY_STEP],
    };
    const d = map[e.key];
    if (!d) return;
    e.preventDefault();
    moveFocus(d[0], d[1]);
  };

  const grab = useCallback(async (asPerson = false) => {
    if (!active || grabbing || (poolFull && !asPerson)) return;
    // Pause first: the grabbed timestamp must be the second the user is
    // LOOKING at. On a playing video the clock advances between the click and
    // the read, and the server would re-read a frame the user never saw.
    videoRef.current?.pause();
    const at = videoRef.current?.currentTime ?? current;
    setGrabbing(true);
    setGrabError(null);
    try {
      const res = await grabCoverFrame(active.id, at);
      onGrabbed(
        {
          generatedMediaId: res.generated_media_id,
          url: res.url,
          timestampSeconds: res.timestamp_seconds,
        },
        asPerson,
      );
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

  const applyFrameAsCover = useCallback(async () => {
    if (!active || !onUseAsCover || using) return;
    videoRef.current?.pause();
    const at = videoRef.current?.currentTime ?? current;
    setUsing(true);
    setGrabError(null);
    try {
      await onUseAsCover(active.id, at, focus, zoom);
    } catch (err) {
      console.error('[CoverFrameGrabber] use-as-cover failed:', err);
      setGrabError(
        t(
          'distribution.coverStudio.useFrameFailed',
          'That frame could not be turned into a cover. Try another moment.',
        ),
      );
    } finally {
      setUsing(false);
    }
  }, [active, onUseAsCover, using, current, focus, zoom, t]);

  const uploadCover = useCallback(
    async (file: File) => {
      if (!onUploadCover) return;
      if (!(file.type || '').toLowerCase().startsWith('image/')) {
        setGrabError(
          t('distribution.coverStudio.templateNeedsImage', 'A cover template has to be an image.'),
        );
        return;
      }
      setUsing(true);
      setGrabError(null);
      try {
        await onUploadCover(file);
      } catch (err) {
        console.error('[CoverFrameGrabber] upload cover failed:', err);
        setGrabError(
          t(
            'distribution.coverStudio.uploadCoverFailed',
            'That picture could not be uploaded as the cover. Try again.',
          ),
        );
      } finally {
        setUsing(false);
      }
    },
    [onUploadCover, t],
  );

  const picker = (
    <div className="cs-body" data-testid="cover-video-picker">
      {libraryError ? (
        <div className="cs-error">
          {t('distribution.coverStudio.libraryLoadFailed', 'Could not load your library.')}
          <button type="button" onClick={() => void loadLibrary()}>
            {t('common.retry', 'Retry')}
          </button>
        </div>
      ) : library === null ? (
        <div className="cs-empty">{t('common.loading', 'Loading…')}</div>
      ) : library.length === 0 ? (
        <div className="cs-empty">
          {t('distribution.coverStudio.libraryNoVideos', 'No videos in your library yet.')}
        </div>
      ) : (
        <div className="cs-videopick">
          {library.map((v) => (
            <button
              key={v.id}
              type="button"
              title={v.filename}
              onClick={() => pickVideo(v)}
              data-testid={`cover-video-pick-${v.id}`}
            >
              {v.thumbnail_url ? <img src={v.thumbnail_url} alt="" /> : <span className="noimg" />}
              <span className="name">{v.filename}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );

  if (!active || choosing) {
    // With a scope we can offer the library right here; without one there is
    // nothing to offer, and an enabled control that cannot work is worse than
    // saying what is missing (CoverPicker's own rule).
    return (
      <div className={embedded ? 'cs-stagewrap' : 'cs-card'}>
        <h4>
          {t('distribution.coverStudio.pickVideo', 'Pick a video to grab frames from')}
          {choosing && (
            <button type="button" className="cs-ghost aux" onClick={() => setChoosing(false)}>
              {t('common.cancel', 'Cancel')}
            </button>
          )}
        </h4>
        {scopeId ? (
          picker
        ) : (
          <div className="cs-empty">
            {t(
              'distribution.coverStudio.grabNeedsVideo',
              'Pick a video on the publish page first — frames are grabbed from it.',
            )}
          </div>
        )}
      </div>
    );
  }

  const src = getResourceFileUrl(active.id, token);
  const progress = duration > 0 ? (current / duration) * 100 : 0;

  return (
    <div className={embedded ? 'cs-stagewrap' : 'cs-card'}>
      <h4>
        {embedded
          ? t('distribution.coverStudio.stageFrame', {
              defaultValue: 'Video frame {{at}}',
              at: formatTimestamp(current),
            })
          : t('distribution.coverStudio.grabFrame', 'Grab a frame')}
        <span className="aux">
          {embedded
            ? aspect === '4:3'
              ? t('distribution.coverStudio.cropGuideH', 'Drag the box · horizontal 4:3')
              : t('distribution.coverStudio.cropGuideV', 'Drag the box · vertical 3:4')
            : formatTimestamp(current)}
          {embedded && (
            <HelpTip
              text={t(
                'distribution.coverStudio.grabDots',
                'Dots are frames you already took. The frame is re-read from the source file at full quality — not a screenshot of the player.',
              )}
            />
          )}
        </span>
      </h4>
      <div className="cs-body">
        {sources.length <= 1 && scopeId && (
          <button
            type="button"
            className="cs-ghost cs-change-video"
            onClick={() => {
              setChoosing(true);
              if (library === null) void loadLibrary();
            }}
            data-testid="cover-change-video"
          >
            {t('distribution.coverStudio.changeVideo', 'Change video')} · {active.filename}
          </button>
        )}
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

        <div className="cs-stage" ref={stageRef}>
          {/* crossOrigin so a future canvas use never taints; preload metadata
              so duration arrives without pulling the whole file. */}
          <video
            ref={videoRef}
            src={src}
            preload="metadata"
            controls={false}
            data-testid="cover-grab-video"
            style={embedded ? videoStyle() : undefined}
            onLoadedMetadata={(e) => {
              setDuration(e.currentTarget.duration || 0);
              setVideoSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight });
            }}
            onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime || 0)}
            onClick={() => {
              const v = videoRef.current;
              if (!v) return;
              if (v.paused) void v.play();
              else v.pause();
            }}
          />
          {/* The crop box — what the server keeps. Drag it (or nudge it with
              the arrow keys); its centre is sent as the crop anchor and
              covers/select honours it. Until the video's size is known the
              box is drawn centred by CSS. */}
          {embedded && (
            <div
              className={`cs-cropguide ${aspect === '4:3' ? 'h' : 'v'} ${videoSize ? 'placed' : ''}`}
              style={guideStyle()}
              role="slider"
              tabIndex={0}
              aria-label={t('distribution.coverStudio.cropBox', 'Crop box — drag the picture or use the arrow keys')}
              aria-valuetext={`${Math.round(focus.x * 100)}%, ${Math.round(focus.y * 100)}%`}
              data-focus-x={focus.x.toFixed(2)}
              data-focus-y={focus.y.toFixed(2)}
              data-zoom={zoom.toFixed(1)}
              data-testid="cover-crop-guide"
              onPointerDown={onGuidePointerDown}
              onPointerMove={onGuidePointerMove}
              onPointerUp={onGuidePointerUp}
              onPointerCancel={onGuidePointerUp}
              onKeyDown={onGuideKeyDown}
            />
          )}
        </div>

        {embedded && (
          <div className="cs-zoom" data-testid="cover-zoom">
            <button type="button" aria-label={t('distribution.coverStudio.zoomOut', 'Zoom out')} onClick={() => setZoom((z) => Math.max(1, +(z - 0.25).toFixed(2)))}>
              <ZoomOut size={13} />
            </button>
            <input
              type="range"
              min={1}
              max={4}
              step={0.05}
              value={zoom}
              aria-label={t('distribution.coverStudio.zoom', 'Zoom')}
              onChange={(e) => setZoom(Number(e.target.value))}
              data-testid="cover-zoom-slider"
            />
            <button type="button" aria-label={t('distribution.coverStudio.zoomIn', 'Zoom in')} onClick={() => setZoom((z) => Math.min(4, +(z + 0.25).toFixed(2)))}>
              <ZoomIn size={13} />
            </button>
            <span className="val">{zoom.toFixed(1)}×</span>
          </div>
        )}

        <div
          className={`cs-track ${strip.candidates.length > 0 ? 'strip' : ''}`}
          data-testid="cover-grab-track"
          role="slider"
          aria-label={t('distribution.coverStudio.grabTimeline', 'Timeline')}
          aria-valuemin={0}
          aria-valuemax={duration}
          aria-valuenow={current}
        >
          {strip.candidates.length > 0 && (
            <div className="frames" aria-hidden="true" data-testid="cover-filmstrip">
              {strip.candidates.map((c) => (
                <img key={c.index} src={c.preview_data_url} alt="" draggable={false} />
              ))}
            </div>
          )}
          {strip.status === 'sampling' && (
            <span className="sampling" data-testid="cover-filmstrip-sampling">
              {t('distribution.coverStudio.sampling', 'Sampling frames…')}
            </span>
          )}
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
          <span className="cursor" style={{ left: `${progress}%` }} />
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

        <div className="cs-stage-actions row">
          <button
            type="button"
            className="cs-grab"
            disabled={grabbing || poolFull}
            onClick={() => void grab(false)}
            title={t('distribution.coverStudio.grabThisWhy', 'Adds this frame to the pictures sent to the model. Re-read from the source file at full quality — not a screenshot of the player.')}
            data-testid="cover-grab-button"
          >
            <Camera size={14} />
            {grabbing
              ? t('distribution.coverStudio.grabbing', 'Grabbing…')
              : poolFull
                ? t('distribution.coverStudio.grabPoolFull', 'Pool is full (9/9)')
                : t('distribution.coverStudio.grabThis', 'Grab this frame')}
          </button>
          <button
            type="button"
            className="cs-grab alt"
            disabled={grabbing}
            onClick={() => void grab(true)}
            title={t('distribution.coverStudio.grabAsPersonWhy', 'The style keeps the same face across every draft. Grab a frame of yourself; it replaces the previous person.')}
            data-testid="cover-grab-person"
          >
            <User size={14} />
            {t('distribution.coverStudio.setAsPerson', 'Set as person')}
          </button>
          {onUseAsCover && (
            <button
              type="button"
              className="cs-grab alt"
              disabled={using || grabbing}
              onClick={() => void applyFrameAsCover()}
              data-testid="cover-use-frame"
            >
              <Check size={14} />
              {using
                ? t('distribution.coverStudio.applying', 'Saving…')
                : t('distribution.coverStudio.useFrameShort', 'Use this crop as the cover')}
            </button>
          )}
          {onUploadCover && (
            <>
              <button
                type="button"
                className="cs-grab alt"
                disabled={using}
                onClick={() => uploadRef.current?.click()}
                data-testid="cover-upload-cover"
              >
                <Upload size={14} />
                {t('distribution.coverStudio.uploadCoverShort', 'Upload')}
              </button>
              <input
                ref={uploadRef}
                type="file"
                accept="image/*"
                hidden
                data-testid="cover-upload-cover-input"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void uploadCover(f);
                  e.target.value = '';
                }}
              />
            </>
          )}
        </div>

        {grabError && (
          <div className="cs-error" style={{ margin: '10px 0 0' }} data-testid="cover-grab-error">
            {grabError}
          </div>
        )}

      </div>
    </div>
  );
}
