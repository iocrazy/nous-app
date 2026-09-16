import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, Pause, Volume1, Volume2, VolumeX, Maximize, SkipBack, SkipForward, Settings } from 'lucide-react';
import Hls from 'hls.js';
import ErrorPage from './ErrorPage';
import {
  resumeKeyFor,
  savePosition,
  loadPosition,
  clearPosition,
  getEntry,
  markSynced,
  adoptRemote,
  reconcile,
  isResumable,
} from '../utils/playbackResume';
import {
  fetchRemotePosition,
  pushRemotePosition,
  deleteRemotePosition,
} from '../services/playbackSyncService';
import { useOptionalAuth } from '../contexts/AuthContext';

interface HlsLevel {
  height: number;
  width: number;
  bitrate: number;
  name?: string;
}

interface VideoPlayerProps {
  src: string;
  originalSrc?: string;  // Direct file URL (non-HLS) for "Original" quality option
  mimeType?: string;
  fps?: number;
  authToken?: string;
  onTimeUpdate: (seconds: number) => void;
  onDurationChange: (seconds: number) => void;
  playerRef: React.RefObject<HTMLVideoElement | null>;
  onToggleShortcuts?: () => void;
  commentMarkers?: Array<{ time: number; color?: string }>;
  /**
   * Stable identity for remembering where playback got to. Pass the resource /
   * media id — NOT the URL: `src` carries a rotating `?token=` and changes path
   * between the HLS playlist and the original file, so two spellings of the
   * same video would each get their own position. Omitted, the player falls
   * back to the URL path (see `resumeKeyFor`).
   */
  resumeKey?: string;
}

const QUALITY_PREF_KEY = 'mediahub_quality_pref';
const VOLUME_PREF_KEY = 'mediahub_volume_pref';

/** How often a playing video writes its position. Once a second is plenty —
 * `timeupdate` fires ~4x that, and this is a localStorage write. */
const POSITION_SAVE_INTERVAL_MS = 1000;

/** How often that position goes to the SERVER while playing.
 *
 * Two orders of magnitude rarer than the local write, and deliberately so: the
 * local store exists precisely so the cross-device layer does not have to keep
 * up with a per-second signal. Fifteen seconds is the most a viewer can lose
 * by killing the tab in a way that skips `pagehide`; every ordinary exit
 * (pause, end, navigate, close) flushes immediately. */
const REMOTE_SYNC_INTERVAL_MS = 15_000;

/** A remote position closer than this to where we already are is not worth a
 * seek — the jump would be visible and would gain the viewer nothing. */
const MIN_SEEK_DELTA_SECONDS = 2;

/** Below this, a push carries no information the server does not already have.
 * Generous enough to absorb the float noise of a paused element reporting its
 * own position, far below anything a viewer would notice losing. */
const PUSH_EPSILON_SECONDS = 0.5;

/** Shared chrome for every popup in the control bar, so speed / volume /
 * quality cannot drift apart visually. */
const MENU_SURFACE =
  'bg-ink-900/95 backdrop-blur-sm border border-ink-700 rounded-lg shadow-xl';

const menuItemClass = (active: boolean): string =>
  `w-full px-3 py-1.5 text-xs text-right font-mono transition-colors ${
    active
      ? 'text-[var(--accent-text)] bg-[var(--accent-soft)]'
      : 'text-ink-300 hover:bg-ink-800 hover:text-white'
  }`;

/** A control-bar button. One place so icon and text buttons line up. */
const BAR_BUTTON =
  'px-2 py-1 rounded text-ink-300 hover:text-white hover:bg-white/10 transition-colors';

const readStoredVolume = (): { volume: number; muted: boolean } => {
  try {
    const raw = localStorage.getItem(VOLUME_PREF_KEY);
    if (!raw) return { volume: 1, muted: false };
    const parsed = JSON.parse(raw) as { volume?: number; muted?: boolean };
    const v = typeof parsed.volume === 'number' && parsed.volume >= 0 && parsed.volume <= 1
      ? parsed.volume
      : 1;
    return { volume: v, muted: parsed.muted === true };
  } catch (err) {
    console.error('[VideoPlayer] volume pref read failed', err);
    return { volume: 1, muted: false };
  }
};

const writeStoredVolume = (volume: number, muted: boolean): void => {
  try {
    localStorage.setItem(VOLUME_PREF_KEY, JSON.stringify({ volume, muted }));
  } catch (err) {
    console.error('[VideoPlayer] volume pref write failed', err);
  }
};

const formatTime = (seconds: number): string => {
  if (!isFinite(seconds) || isNaN(seconds)) return '00:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
};

const formatTimeWithFrames = (seconds: number, fps: number): string => {
  if (!isFinite(seconds) || isNaN(seconds)) return '00:00.00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const fractional = seconds % 1;
  const frameInSecond = Math.floor(fractional * fps);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${frameInSecond.toString().padStart(2, '0')}`;
};

export const VideoPlayer: React.FC<VideoPlayerProps> = ({
  src,
  originalSrc,
  mimeType,
  fps = 30,
  authToken,
  onTimeUpdate,
  onDurationChange,
  playerRef,
  onToggleShortcuts,
  commentMarkers,
  resumeKey,
}) => {
  const hlsRef = useRef<Hls | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hideControlsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const volumeCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedAt = useRef(0);
  const lastPushedAt = useRef(0);
  /**
   * Has this media's open-time reconcile finished?
   *
   * Until it has, this device does not know whether another one is further
   * along, so ANY push from here is a blind last-writer-wins that can clobber
   * a newer position. Observed on production before this gate existed: the
   * periodic push fires on the first `timeupdate` after mount (its throttle
   * window starts satisfied), and the resume seek itself produces that
   * `timeupdate` — so opening a video on device A overwrote the position
   * device B had just written, every time.
   *
   * Losing a push inside this window costs a few seconds that localStorage
   * still holds and the next open will send. Clobbering costs another
   * device's real progress. The trade is not close.
   */
  const syncSettled = useRef(false);
  /**
   * The position the SERVER is known to hold, as far as this player can tell:
   * seeded from the open-time reconcile, then updated by each successful push.
   *
   * It exists to stop this device RE-ASSERTING a position it never changed.
   * Observed on production: reload fires both the `pagehide` flush and the
   * unmount flush, each pushing the same untouched position — so simply
   * reopening a video overwrote the newer position another device had written
   * while this tab sat paused. A push that carries no new information cannot
   * be worth destroying someone else's.
   *
   * `null` = the server is not known to hold anything, so any push is news.
   */
  const serverHoldsPosition = useRef<number | null>(null);
  /**
   * Which ATTACHMENT has already been seeked, not which media.
   *
   * Two different events both fire `loadedmetadata` and they need opposite
   * treatment:
   *   - a quality switch restores its own position and fires metadata again,
   *     so seeking a second time would undo it;
   *   - the source-attach effect re-running (the auth token churns, or the
   *     browser drops a backgrounded tab's media) genuinely resets the element
   *     to zero and nothing else will put the viewer back.
   *
   * Keying on the media made the first case right and the second wrong: a
   * video paused at 05:13, the user switched browser tabs, came back, and it
   * was on frame 0 — the restore was refused because that media had "already
   * resumed". Keying on the attachment tells them apart.
   */
  const attachmentId = useRef(0);
  const resumedForAttachment = useRef(-1);
  /** Where the element was just before the current attachment replaced it —
   * only when it is the SAME media. Fresher than storage, which lags by up to
   * the save throttle. */
  const carriedPosition = useRef<number | null>(null);
  /** The element's live position, kept current so the value above has
   * something truthful to carry. */
  const livePosition = useRef(0);
  /** The media the previous attachment was for, to tell "same video, new
   * token" from "a different video". */
  const attachedKey = useRef<string | null>(null);
  /** Consecutive fatal HLS errors we have tried to recover from. */
  const hlsRecoveryAttempts = useRef(0);

  const storedVolume = useRef(readStoredVolume());
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [volume, setVolume] = useState(storedVolume.current.volume);
  const [isMuted, setIsMuted] = useState(storedVolume.current.muted);
  const [showVolumeSlider, setShowVolumeSlider] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showControls, setShowControls] = useState(true);
  const [resolution, setResolution] = useState<{ width: number; height: number } | null>(null);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const [hlsLevels, setHlsLevels] = useState<HlsLevel[]>([]);
  const [currentHlsLevel, setCurrentHlsLevel] = useState(-1);
  const [isAutoQuality, setIsAutoQuality] = useState(true);
  const [showQualityMenu, setShowQualityMenu] = useState(false);
  const [isOriginalMode, setIsOriginalMode] = useState(false); // Playing original file directly
  const [authError, setAuthError] = useState<401 | 403 | null>(null);

  const effectiveFps = fps || 30;
  const isHls = new URL(src, window.location.origin).pathname.endsWith('.m3u8');
  const positionKey = resumeKeyFor(resumeKey, src);
  // `useOptionalAuth`, not `useAuth`: this component is rendered in isolation
  // by its own tests and by the cover grabber, neither of which mounts the
  // provider. A missing provider means "signed out", which the store already
  // has a namespace for.
  const viewerId = useOptionalAuth()?.currentUserId ?? null;

  /**
   * Reconcile this device against the server when the media opens.
   *
   * Order matters. An unsynced local entry is pushed FIRST, because
   * `reconcile` cannot tell "written offline a minute ago" from "stale since
   * last week" and resolves that ambiguity in the server's favour — pushing
   * first removes the ambiguity instead of losing to it.
   *
   * Only ever seeks FORWARD-or-back to a position the viewer has actually
   * reached; `isResumable` re-checks the floor and end-margin against THIS
   * device's duration, because a server value has never been through them.
   */
  const syncOnOpen = useCallback(
    async (video: HTMLVideoElement) => {
      if (!viewerId) return;
      const duration = video.duration;
      if (!Number.isFinite(duration) || duration <= 0) {
        // Nothing to reconcile against and nothing worth pushing either, so
        // opening the gate here costs nothing and avoids wedging it shut.
        syncSettled.current = true;
        return;
      }

      try {
        const local = getEntry(viewerId, positionKey);
        if (local && !local.sv) {
          // Bypasses `pushPosition` deliberately: the gate is still shut, and
          // this is the one push that must happen before it opens.
          const sv = await pushRemotePosition(positionKey, local.t, local.d);
          if (sv) markSynced(viewerId, positionKey, sv);
          // Not seeded here: the fetch below reads back whatever actually
          // landed, which is the authority.

        }

        const remote = await fetchRemotePosition(positionKey);
        // Seed BEFORE acting on the decision: whatever the server answered is
        // what it holds, whether or not we go on to adopt it. If it holds
        // nothing, every later push is news.
        serverHoldsPosition.current = remote ? remote.position_seconds : null;

        const decision = reconcile(getEntry(viewerId, positionKey), remote);
        if (decision.source !== 'remote' || !decision.serverUpdatedAt) return;
        if (!isResumable(decision.seconds, duration)) return;

        const seconds = decision.seconds as number;
        adoptRemote(viewerId, positionKey, seconds, duration, decision.serverUpdatedAt);

        // Two reasons not to move the playhead, even though the value is worth
        // storing either way:
        //   - the viewer already pressed play on THIS device while the request
        //     was in flight; yanking them mid-sentence is worse than being two
        //     minutes behind another device;
        //   - we are already essentially there, so the seek would be a visible
        //     jump that gains nothing.
        if (!video.paused) return;
        if (Math.abs(video.currentTime - seconds) <= MIN_SEEK_DELTA_SECONDS) return;

        video.currentTime = seconds;
        setCurrentTime(seconds);
        onTimeUpdate(seconds);
      } finally {
        // Open the gate even on failure: a device that could not reach the
        // server must still record progress locally AND be able to push it
        // once the network returns, or an offline session never syncs at all.
        syncSettled.current = true;
        // Start the periodic window now, so the first push is a full interval
        // away rather than on the very next `timeupdate`.
        lastPushedAt.current = Date.now();
      }
    },
    [viewerId, positionKey, onTimeUpdate],
  );

  /**
   * Push the current position to the server and record that it is ours.
   *
   * Signed-out viewing (a share link) stays local-only: there is no account to
   * sync to, and the server would have nowhere to put it.
   */
  const pushPosition = useCallback(
    async (t: number, d: number, opts: { keepalive?: boolean } = {}) => {
      if (!viewerId) return;
      if (!syncSettled.current) return; // see `syncSettled`
      // Nothing new to say — and saying it anyway overwrites whatever another
      // device wrote in the meantime. See `serverHoldsPosition`.
      if (
        serverHoldsPosition.current !== null &&
        Math.abs(t - serverHoldsPosition.current) < PUSH_EPSILON_SECONDS
      ) {
        return;
      }
      const sv = await pushRemotePosition(positionKey, t, d, opts);
      if (sv) {
        serverHoldsPosition.current = Math.min(t, d);
        markSynced(viewerId, positionKey, sv);
      }
    },
    [viewerId, positionKey],
  );

  // Frame stepping
  const stepFrame = useCallback((direction: 1 | -1, count: number = 1) => {
    if (!playerRef.current) return;
    const video = playerRef.current;
    if (!video.paused) return;
    const frameDuration = 1 / effectiveFps;
    const newTime = Math.max(0, Math.min(video.duration, video.currentTime + direction * count * frameDuration));
    video.currentTime = newTime;
    setCurrentTime(newTime);
    onTimeUpdate(newTime);
  }, [playerRef, effectiveFps, onTimeUpdate]);

  // Seek relative to current position (works in both playing and paused states)
  const seekRelative = useCallback((seconds: number) => {
    const video = playerRef.current;
    if (!video) return;
    const newTime = Math.max(0, Math.min(video.duration || 0, video.currentTime + seconds));
    video.currentTime = newTime;
    setCurrentTime(newTime);
    onTimeUpdate(newTime);
  }, [playerRef, onTimeUpdate]);

  // Change playback speed
  const changeSpeed = useCallback((rate: number) => {
    const video = playerRef.current;
    if (!video) return;
    const clamped = Math.max(0.25, Math.min(3, rate));
    video.playbackRate = clamped;
    setPlaybackRate(clamped);
    setShowSpeedMenu(false);
  }, [playerRef]);

  // Change HLS quality level (-1 = auto)
  const changeQuality = useCallback((levelIndex: number) => {
    const video = playerRef.current;
    if (!video) return;

    // If currently in original mode, re-create HLS instance
    if (isOriginalMode && isHls && Hls.isSupported()) {
      setIsOriginalMode(false);
      const hlsConfig: Partial<Hls['config']> = {};
      if (authToken) {
        hlsConfig.xhrSetup = (xhr: XMLHttpRequest) => {
          xhr.setRequestHeader('Authorization', `Bearer ${authToken}`);
        };
      }
      const pos = video.currentTime;
      const wasPlaying = !video.paused;
      const hls = new Hls(hlsConfig);
      hlsRef.current = hls;
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        hls.currentLevel = levelIndex;
        video.currentTime = pos;
        if (wasPlaying) video.play().catch(() => {});
      });
      hls.on(Hls.Events.LEVEL_SWITCHED, (_event, data) => {
        setCurrentHlsLevel(data.level);
      });
      setCurrentHlsLevel(levelIndex >= 0 ? levelIndex : -1);
      setIsAutoQuality(levelIndex === -1);
      setShowQualityMenu(false);
      return;
    }

    const hls = hlsRef.current;
    if (!hls) return;

    hls.currentLevel = levelIndex;
    setCurrentHlsLevel(levelIndex >= 0 ? levelIndex : hls.currentLevel);
    setIsAutoQuality(levelIndex === -1);
    setShowQualityMenu(false);

    // Persist quality preference by height (stable across playlist rewrites)
    if (levelIndex === -1) {
      localStorage.setItem(QUALITY_PREF_KEY, 'auto');
    } else if (levelIndex >= 0 && hlsLevels[levelIndex]) {
      localStorage.setItem(QUALITY_PREF_KEY, `${hlsLevels[levelIndex].height}p`);
    }

    // Force reload current position when paused to make switch visible
    if (video.paused && levelIndex >= 0) {
      const pos = video.currentTime;
      setTimeout(() => {
        if (video.currentTime === pos) {
          video.currentTime = pos; // trigger fragment load
        }
      }, 100);
    }
  }, [isOriginalMode, isHls, authToken, src, playerRef, hlsLevels]);

  // Switch to original (non-HLS) direct file playback
  const switchToOriginal = useCallback(() => {
    if (!originalSrc || !playerRef.current) return;
    const video = playerRef.current;
    const pos = video.currentTime;
    const wasPlaying = !video.paused;

    // Destroy HLS instance
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    // Switch to direct file
    video.src = originalSrc;
    video.currentTime = pos;
    if (wasPlaying) video.play().catch(() => {});

    setIsOriginalMode(true);
    setIsAutoQuality(false);
    setShowQualityMenu(false);

    // Persist quality preference
    localStorage.setItem(QUALITY_PREF_KEY, 'original');
  }, [originalSrc, playerRef]);

  // Attach or detach HLS / native source
  useEffect(() => {
    const video = playerRef.current;
    if (!video) return;

    setIsLoading(true);
    setLoadError(null);
    setAuthError(null);
    setIsPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    setResolution(null);
    setHlsLevels([]);
    setCurrentHlsLevel(-1);
    setIsAutoQuality(true);
    setBuffered(0);
    hlsRecoveryAttempts.current = 0;
    syncSettled.current = false;
    serverHoldsPosition.current = null;

    // A new attachment begins here. Carry the viewer's place across it only
    // when it is the same video: a token refresh must be invisible, while
    // navigating to a different video must NOT inherit the old position.
    const sameMedia = attachedKey.current === positionKey;
    carriedPosition.current = sameMedia
      ? Math.max(video.currentTime || 0, livePosition.current)
      : null;
    if (!sameMedia) {
      livePosition.current = 0;
    }
    attachedKey.current = positionKey;
    attachmentId.current += 1;

    if (isHls && Hls.isSupported()) {
      const hlsConfig: Partial<Hls['config']> = {};
      if (authToken) {
        hlsConfig.xhrSetup = (xhr: XMLHttpRequest) => {
          xhr.setRequestHeader('Authorization', `Bearer ${authToken}`);
        };
      }
      const hls = new Hls(hlsConfig);
      hlsRef.current = hls;
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, (_event, data) => {
        setIsLoading(false);
        const levels = data.levels.map((l) => ({
          height: l.height,
          width: l.width,
          bitrate: l.bitrate,
          name: (l as unknown as Record<string, unknown>).attrs
            ? ((l as unknown as Record<string, Record<string, string>>).attrs?.NAME || undefined)
            : undefined,
        }));
        setHlsLevels(levels);

        // Restore quality preference from localStorage (stored as height, e.g. "720p")
        const pref = localStorage.getItem(QUALITY_PREF_KEY);
        if (pref === 'original' && originalSrc) {
          // Will switch to original after HLS init completes
          setTimeout(() => switchToOriginal(), 0);
        } else if (pref && pref !== 'auto') {
          const matchIdx = levels.findIndex(l => `${l.height}p` === pref);
          if (matchIdx >= 0) {
            hls.currentLevel = matchIdx;
            setIsAutoQuality(false);
          }
        }
      });
      hls.on(Hls.Events.LEVEL_SWITCHED, (_event, data) => {
        setCurrentHlsLevel(data.level);
      });
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return;
        // A fatal hls.js error is NOT necessarily the end of playback, and
        // treating it as one is what made a deploy look like a dead player:
        // `registerType: 'autoUpdate'` swaps the service worker mid-session
        // (skipWaiting + clientsClaim), and segment requests in flight through
        // the retiring worker fail. Before this, the handler only hid the
        // spinner — hls.js stops loading on a fatal error, so the video sat
        // frozen until the user reloaded the page.
        //
        // The two recoverable classes have documented recoveries. Bounded,
        // because retrying a genuinely broken stream forever is a spinner that
        // never resolves and a request loop nobody asked for.
        const MAX_RECOVERY_ATTEMPTS = 3;
        if (hlsRecoveryAttempts.current >= MAX_RECOVERY_ATTEMPTS) {
          setIsLoading(false);
          setLoadError('Playback failed after several recovery attempts');
          hls.destroy();
          return;
        }
        hlsRecoveryAttempts.current += 1;
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          console.error('[VideoPlayer] fatal HLS network error, restarting load', data.details);
          setIsLoading(true);
          hls.startLoad();
          return;
        }
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          console.error('[VideoPlayer] fatal HLS media error, recovering', data.details);
          setIsLoading(true);
          hls.recoverMediaError();
          return;
        }
        console.error('[VideoPlayer] unrecoverable HLS error', data.type, data.details);
        setIsLoading(false);
        setLoadError('Video failed to load');
        hls.destroy();
      });
      // Any successfully loaded fragment means the stream is healthy again, so
      // an earlier blip must not count against a later, unrelated one.
      hls.on(Hls.Events.FRAG_BUFFERED, () => {
        hlsRecoveryAttempts.current = 0;
      });
    } else if (isHls && video.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS support (Safari)
      video.src = src;
    } else {
      video.src = src;
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
    // `positionKey` is listed because the carry-over above reads it. In
    // practice it never fires this effect on its own — it is derived from
    // `src` (or an explicit `resumeKey` the caller changes alongside the
    // source), so `src` already covers every real case.
  }, [src, isHls, authToken, playerRef, originalSrc, switchToOriginal, positionKey]);

  // Video event listeners
  useEffect(() => {
    const video = playerRef.current;
    if (!video) return;

    const updateBuffered = () => {
      try {
        const ranges = video.buffered;
        if (!ranges || ranges.length === 0) return;
        const t = video.currentTime;
        // The range that contains the playhead — not `end(length-1)`, which
        // after a seek reports a far-away island as if it were continuous
        // buffer and paints a full bar over an empty one.
        for (let i = 0; i < ranges.length; i++) {
          if (ranges.start(i) <= t && t <= ranges.end(i)) {
            setBuffered(ranges.end(i));
            return;
          }
        }
        setBuffered(t);
      } catch (err) {
        // `buffered` throws on a detached element in some browsers.
        console.error('[VideoPlayer] buffered read failed', err);
      }
    };

    const handleTimeUpdate = () => {
      const t = video.currentTime;
      livePosition.current = t;
      setCurrentTime(t);
      onTimeUpdate(t);
      updateBuffered();
      const now = Date.now();
      if (now - lastSavedAt.current >= POSITION_SAVE_INTERVAL_MS) {
        lastSavedAt.current = now;
        savePosition(viewerId, positionKey, t, video.duration);
      }
      if (now - lastPushedAt.current >= REMOTE_SYNC_INTERVAL_MS) {
        lastPushedAt.current = now;
        void pushPosition(t, video.duration);
      }
    };

    const handleDurationChange = () => {
      const d = video.duration;
      if (isFinite(d)) {
        setDuration(d);
        onDurationChange(d);
      }
    };

    const handleLoadedMetadata = () => {
      setIsLoading(false);
      if (video.videoWidth && video.videoHeight) {
        setResolution({ width: video.videoWidth, height: video.videoHeight });
      }
      if (isFinite(video.duration)) {
        setDuration(video.duration);
        onDurationChange(video.duration);
      }

      // Apply the remembered volume to the element. State already carries it
      // (it seeds from storage), but the element is new on every source
      // attach and defaults to 1.0 / unmuted.
      video.volume = volume;
      video.muted = isMuted;

      // Resume — once per ATTACHMENT (see `attachmentId`).
      if (resumedForAttachment.current !== attachmentId.current) {
        resumedForAttachment.current = attachmentId.current;

        // A position carried across a re-attach of the same media wins over
        // storage: it is where the viewer actually was, whereas the stored
        // value can be up to one save-interval stale.
        //
        // Local before remote, applied synchronously: it is already here, and
        // a viewer should not watch the first seconds twice while a request
        // flies. The server answer arrives below and corrects it if another
        // device is further along.
        const carried = carriedPosition.current;
        carriedPosition.current = null;
        const saved =
          carried !== null && carried > 0
            ? carried
            : loadPosition(viewerId, positionKey, video.duration);
        if (saved !== null) {
          video.currentTime = saved;
          livePosition.current = saved;
          setCurrentTime(saved);
          onTimeUpdate(saved);
        }

        if (viewerId) {
          // The service swallows its own errors, but a caller that lets a
          // rejection escape `void` would surface as an unhandled rejection
          // in someone else's console. Log and move on — the `finally` inside
          // has already opened the gate.
          syncOnOpen(video).catch((err) =>
            console.error('[VideoPlayer] open-time sync failed', err),
          );
        } else {
          // Signed out: nothing to reconcile, and `pushPosition` no-ops anyway.
          syncSettled.current = true;
        }
      }
    };

    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => {
      setIsPlaying(false);
      // Pausing is the strongest signal that this position matters; don't wait
      // for either throttle window.
      lastSavedAt.current = Date.now();
      lastPushedAt.current = Date.now();
      savePosition(viewerId, positionKey, video.currentTime, video.duration);
      void pushPosition(video.currentTime, video.duration);
    };
    const handleEnded = () => {
      setIsPlaying(false);
      // Finished — the next open starts clean rather than at the old midpoint,
      // on THIS device and on every other one.
      clearPosition(viewerId, positionKey);
      if (viewerId) void deleteRemotePosition(positionKey);
    };
    const handleWaiting = () => setIsLoading(true);
    const handleCanPlay = () => {
      setIsLoading(false);
      updateBuffered();
    };
    const handleError = () => {
      setIsLoading(false);
      // Check if error is auth-related via HEAD request
      if (src) {
        fetch(src, { method: 'HEAD' })
          .then((res) => {
            if (res.status === 401 || res.status === 403) {
              setAuthError(res.status as 401 | 403);
            } else {
              setLoadError('Video failed to load');
            }
          })
          .catch(() => {
            setLoadError('Video failed to load');
          });
      } else {
        setLoadError('Video failed to load');
      }
    };

    const handleResize = () => {
      if (video.videoWidth && video.videoHeight) {
        setResolution({ width: video.videoWidth, height: video.videoHeight });
      }
    };

    video.addEventListener('progress', updateBuffered);
    video.addEventListener('timeupdate', handleTimeUpdate);
    video.addEventListener('durationchange', handleDurationChange);
    video.addEventListener('loadedmetadata', handleLoadedMetadata);
    video.addEventListener('play', handlePlay);
    video.addEventListener('pause', handlePause);
    video.addEventListener('ended', handleEnded);
    video.addEventListener('waiting', handleWaiting);
    video.addEventListener('canplay', handleCanPlay);
    video.addEventListener('error', handleError);
    video.addEventListener('resize', handleResize);

    return () => {
      // The tab can go away without a pause event (deploy-triggered reload,
      // navigation, close). Flush before the listeners come off, otherwise the
      // last up-to-a-second of progress is lost exactly when it is needed.
      savePosition(viewerId, positionKey, video.currentTime, video.duration);
      video.removeEventListener('progress', updateBuffered);
      video.removeEventListener('timeupdate', handleTimeUpdate);
      video.removeEventListener('durationchange', handleDurationChange);
      video.removeEventListener('loadedmetadata', handleLoadedMetadata);
      video.removeEventListener('play', handlePlay);
      video.removeEventListener('pause', handlePause);
      video.removeEventListener('ended', handleEnded);
      video.removeEventListener('waiting', handleWaiting);
      video.removeEventListener('canplay', handleCanPlay);
      video.removeEventListener('error', handleError);
      video.removeEventListener('resize', handleResize);
    };
    // `volume` / `isMuted` are read only to seed a freshly attached element;
    // re-running this effect on every volume tick would thrash the listeners,
    // so they are deliberately not dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playerRef, onTimeUpdate, onDurationChange, positionKey, viewerId, pushPosition, syncOnOpen]);

  // A reload during playback is the whole reason this component remembers
  // anything: the service worker updates in the background and a stale chunk
  // can force `staleChunkReload`. `pagehide` fires in cases `beforeunload`
  // does not (notably iOS Safari), and `visibilitychange` covers a tab put in
  // the background and then discarded.
  useEffect(() => {
    const flush = () => {
      const video = playerRef.current;
      if (!video) return;
      savePosition(viewerId, positionKey, video.currentTime, video.duration);
      // `keepalive` so the request outlives the document. A plain fetch is
      // cancelled when the page goes away — which is precisely the moment this
      // position is worth the most.
      void pushPosition(video.currentTime, video.duration, { keepalive: true });
    };
    const onHidden = () => {
      if (document.visibilityState === 'hidden') flush();
    };
    window.addEventListener('pagehide', flush);
    document.addEventListener('visibilitychange', onHidden);
    return () => {
      window.removeEventListener('pagehide', flush);
      document.removeEventListener('visibilitychange', onHidden);
    };
  }, [playerRef, positionKey, viewerId, pushPosition]);

  // Any popup being open pins the control bar. Fading the bar out from under
  // an open menu takes the menu with it, which reads as the click having done
  // nothing.
  const anyMenuOpen = showSpeedMenu || showQualityMenu || showVolumeSlider;

  // Auto-hide controls
  const resetHideTimer = useCallback(() => {
    setShowControls(true);
    if (hideControlsTimer.current) {
      clearTimeout(hideControlsTimer.current);
    }
    if (isPlaying && !anyMenuOpen) {
      hideControlsTimer.current = setTimeout(() => {
        setShowControls(false);
      }, 3000);
    }
  }, [isPlaying, anyMenuOpen]);

  useEffect(() => {
    if (!isPlaying || anyMenuOpen) {
      setShowControls(true);
      if (hideControlsTimer.current) {
        clearTimeout(hideControlsTimer.current);
      }
    } else {
      resetHideTimer();
    }
    return () => {
      if (hideControlsTimer.current) {
        clearTimeout(hideControlsTimer.current);
      }
    };
  }, [isPlaying, anyMenuOpen, resetHideTimer]);

  const togglePlayPause = useCallback(() => {
    const video = playerRef.current;
    if (!video) return;
    if (video.paused) {
      video.play().catch(() => {});
    } else {
      video.pause();
    }
  }, [playerRef]);

  const handleSeek = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const video = playerRef.current;
      if (!video) return;
      const time = parseFloat(e.target.value);
      video.currentTime = time;
      setCurrentTime(time);
    },
    [playerRef],
  );

  const handleVolumeChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const video = playerRef.current;
      if (!video) return;
      const vol = parseFloat(e.target.value);
      video.volume = vol;
      setVolume(vol);
      const nextMuted = vol === 0;
      video.muted = nextMuted;
      setIsMuted(nextMuted);
      writeStoredVolume(vol, nextMuted);
    },
    [playerRef],
  );

  const toggleMute = useCallback(() => {
    const video = playerRef.current;
    if (!video) return;
    const newMuted = !isMuted;
    video.muted = newMuted;
    setIsMuted(newMuted);
    writeStoredVolume(volume, newMuted);
  }, [playerRef, isMuted, volume]);

  // Hover-to-reveal, with a grace period: the slider sits above the button and
  // the pointer crosses the seam between them. Closing on the first mouseleave
  // would make the control impossible to actually reach.
  const openVolume = useCallback(() => {
    if (volumeCloseTimer.current) clearTimeout(volumeCloseTimer.current);
    setShowVolumeSlider(true);
  }, []);
  const closeVolumeSoon = useCallback(() => {
    if (volumeCloseTimer.current) clearTimeout(volumeCloseTimer.current);
    volumeCloseTimer.current = setTimeout(() => setShowVolumeSlider(false), 260);
  }, []);
  useEffect(
    () => () => {
      if (volumeCloseTimer.current) clearTimeout(volumeCloseTimer.current);
    },
    [],
  );

  const handleFullscreen = useCallback(() => {
    const video = playerRef.current;
    const container = containerRef.current;
    if (!container) return;

    // Standard Fullscreen API (desktop browsers including macOS Safari)
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
      return;
    }
    if (typeof container.requestFullscreen === 'function') {
      container.requestFullscreen().catch(() => {});
      return;
    }

    // iOS Safari fallback: doesn't support requestFullscreen, use native video fullscreen
    if (video && typeof (video as any).webkitEnterFullscreen === 'function') {
      (video as any).webkitEnterFullscreen();
    }
  }, [playerRef]);

  // Comprehensive keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const video = playerRef.current;
      if (!video) return;

      // Ignore if user is typing in an input/textarea
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;

      switch (e.key) {
        case ' ':
          e.preventDefault();
          togglePlayPause();
          break;
        case 'f':
          e.preventDefault();
          handleFullscreen();
          break;
        case 'm':
          e.preventDefault();
          toggleMute();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          seekRelative(-5);
          break;
        case 'ArrowRight':
          e.preventDefault();
          seekRelative(5);
          break;
        case ',':
          e.preventDefault();
          if (video.paused) stepFrame(-1, 1);
          break;
        case '.':
          e.preventDefault();
          if (video.paused) stepFrame(1, 1);
          break;
        case '<':
          e.preventDefault();
          if (video.paused) stepFrame(-1, 10);
          break;
        case '>':
          e.preventDefault();
          if (video.paused) stepFrame(1, 10);
          break;
        case '[':
          e.preventDefault();
          changeSpeed(playbackRate - 0.25);
          break;
        case ']':
          e.preventDefault();
          changeSpeed(playbackRate + 0.25);
          break;
        case '\\':
          e.preventDefault();
          changeSpeed(1);
          break;
        case '?':
          e.preventDefault();
          onToggleShortcuts?.();
          break;
        default:
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [playerRef, stepFrame, seekRelative, togglePlayPause, handleFullscreen, toggleMute, changeSpeed, playbackRate, onToggleShortcuts]);

  const seekProgress = duration > 0 ? (currentTime / duration) * 100 : 0;
  const bufferedProgress = duration > 0 ? Math.min(100, (buffered / duration) * 100) : 0;
  const frameNumber = Math.floor((currentTime || 0) * effectiveFps);

  if (authError) {
    return <ErrorPage code={authError} className="bg-ink-950 rounded-lg" />;
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full bg-black rounded-lg overflow-hidden group"
      onMouseMove={resetHideTimer}
      onMouseLeave={() => {
        if (isPlaying && !anyMenuOpen) setShowControls(false);
      }}
    >
      {/* Video element */}
      <video
        ref={playerRef}
        className="absolute inset-0 w-full h-full object-contain cursor-pointer"
        onClick={togglePlayPause}
        playsInline
        preload="metadata"
      />

      {/* Loading spinner */}
      {isLoading && !loadError && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/40 pointer-events-none">
          <div className="w-10 h-10 border-3 border-ink-600 border-t-white rounded-full animate-spin" />
        </div>
      )}

      {/* Error overlay */}
      {loadError && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/60 pointer-events-none">
          <p className="text-ink-400 text-sm">{loadError}</p>
        </div>
      )}

      {/* Controls overlay */}
      <div
        className={`absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/90 via-black/50 to-transparent pt-10 pb-2 px-3 transition-opacity duration-300 ${
          showControls ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      >
        {/* Seek bar */}
        <div className="relative w-full h-5 flex items-center mb-1 group/seek">
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.1}
            value={currentTime}
            onChange={handleSeek}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
            aria-label="Seek"
          />
          <div className="w-full h-1 group-hover/seek:h-1.5 bg-ink-700 rounded-full transition-all relative overflow-visible">
            {/* Buffered ahead of the playhead. Without it a stall looks like a
                dead player; with it the viewer can see loading happening. */}
            <div
              className="absolute inset-y-0 left-0 bg-ink-500 rounded-full transition-all pointer-events-none"
              style={{ width: `${bufferedProgress}%` }}
            />
            <div
              className="h-full bg-white rounded-full transition-all relative"
              style={{ width: `${seekProgress}%` }}
            >
              <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full opacity-0 group-hover/seek:opacity-100 transition-opacity shadow-md" />
            </div>
            {/* Comment timeline markers */}
            {commentMarkers && duration > 0 && commentMarkers.map((marker, idx) => (
              <div
                key={idx}
                className="absolute top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full pointer-events-none"
                style={{
                  left: `${Math.min(100, (marker.time / duration) * 100)}%`,
                  backgroundColor: marker.color || '#818cf8',
                }}
              />
            ))}
          </div>
        </div>

        {/* Bottom controls row.
            Transport on the left, every adjustment on the right — the layout
            every video site converged on, and the reason the speed menu used
            to open over the middle of the frame. */}
        <div className="flex items-center gap-1">
          {/* ── Left: transport ── */}
          {!isPlaying && (
            <button
              onClick={() => stepFrame(-1)}
              className={`${BAR_BUTTON} py-1.5`}
              aria-label="Previous Frame"
              title="Previous Frame (,)"
            >
              <SkipBack size={14} />
            </button>
          )}

          <button
            onClick={togglePlayPause}
            className="px-2 py-1.5 rounded text-white hover:bg-white/10 transition-colors"
            aria-label={isPlaying ? 'Pause' : 'Play'}
            title={isPlaying ? 'Pause (Space)' : 'Play (Space)'}
          >
            {isPlaying ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}
          </button>

          {!isPlaying && (
            <button
              onClick={() => stepFrame(1)}
              className={`${BAR_BUTTON} py-1.5`}
              aria-label="Next Frame"
              title="Next Frame (.)"
            >
              <SkipForward size={14} />
            </button>
          )}

          {/* Time display with frame number */}
          <span className="text-xs font-mono text-ink-300 select-none ml-1">
            {isPlaying ? (
              <>
                {formatTime(currentTime)} / {formatTime(duration)}
              </>
            ) : (
              <>
                {formatTimeWithFrames(currentTime, effectiveFps)}{' '}
                <span className="text-ink-500">[F{frameNumber}]</span>
                {' / '}
                {formatTime(duration)}
              </>
            )}
          </span>

          <div className="flex-1" />

          {/* ── Right: adjustments ── */}

          {/* Speed */}
          <div className="relative">
            {showSpeedMenu && (
              <div className="fixed inset-0 z-10" onClick={() => setShowSpeedMenu(false)} />
            )}
            <button
              onClick={() => {
                setShowQualityMenu(false);
                setShowSpeedMenu((v) => !v);
              }}
              className={`${BAR_BUTTON} text-xs font-mono ${showSpeedMenu ? 'text-white bg-white/10' : ''}`}
              title="Playback Speed ([ / ] / \\)"
              aria-haspopup="menu"
              aria-expanded={showSpeedMenu}
            >
              {playbackRate === 1 ? '1.0x' : `${playbackRate}x`}
            </button>
            {showSpeedMenu && (
              <div className={`absolute bottom-full right-0 mb-2 py-1 min-w-[84px] z-20 ${MENU_SURFACE}`} role="menu">
                {/* Fastest at the top, like every speed menu users already know. */}
                {[3, 2, 1.5, 1.25, 1, 0.75, 0.5, 0.25].map((rate) => (
                  <button
                    key={rate}
                    onClick={() => changeSpeed(rate)}
                    className={menuItemClass(playbackRate === rate)}
                    role="menuitemradio"
                    aria-checked={playbackRate === rate}
                  >
                    {rate === 1 ? '1.0x' : `${rate}x`}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Volume — hover reveals a vertical slider above the button, click mutes */}
          <div
            className="relative flex items-center"
            onMouseEnter={openVolume}
            onMouseLeave={closeVolumeSoon}
          >
            <button
              onClick={toggleMute}
              onFocus={openVolume}
              className={`${BAR_BUTTON} py-1.5 ${showVolumeSlider ? 'text-white bg-white/10' : ''}`}
              aria-label={isMuted ? 'Unmute' : 'Mute'}
              title={`${isMuted ? 'Unmute' : 'Mute'} (m)`}
            >
              {isMuted || volume === 0 ? (
                <VolumeX size={16} />
              ) : volume < 0.5 ? (
                <Volume1 size={16} />
              ) : (
                <Volume2 size={16} />
              )}
            </button>
            {showVolumeSlider && (
              /* pb-2 lives on the wrapper, not as a margin, so the hover area
                 is continuous from the button to the slider — a gap here makes
                 the popup unreachable with the mouse. */
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 pb-2 z-20">
                <div className={`flex flex-col items-center gap-2 px-2 py-3 ${MENU_SURFACE}`}>
                  <span className="text-[11px] font-mono text-ink-300 tabular-nums select-none">
                    {Math.round((isMuted ? 0 : volume) * 100)}
                  </span>
                  <div className="relative h-24 w-6">
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={isMuted ? 0 : volume}
                      onChange={handleVolumeChange}
                      /* Rotated rather than `writing-mode: vertical-*`: the
                         rotation renders identically everywhere, the
                         writing-mode form is still uneven across browsers. */
                      className="absolute left-1/2 top-1/2 w-24 h-1 -translate-x-1/2 -translate-y-1/2 -rotate-90 accent-white cursor-pointer"
                      aria-label="Volume"
                      aria-orientation="vertical"
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Quality selector (HLS) or static resolution label */}
          {(hlsLevels.length > 1 || (hlsLevels.length > 0 && originalSrc)) ? (
            <div className="relative">
              {showQualityMenu && (
                <div className="fixed inset-0 z-10" onClick={() => setShowQualityMenu(false)} />
              )}
              <button
                onClick={() => {
                  setShowSpeedMenu(false);
                  setShowQualityMenu((v) => !v);
                }}
                className={`${BAR_BUTTON} text-xs font-mono ${showQualityMenu ? 'text-white bg-white/10' : ''}`}
                title="Quality"
                aria-haspopup="menu"
                aria-expanded={showQualityMenu}
              >
                {isOriginalMode
                  ? 'Original'
                  : isAutoQuality
                    ? `Auto${currentHlsLevel >= 0 && hlsLevels[currentHlsLevel]
                        ? ` (${hlsLevels[currentHlsLevel].height}p)`
                        : ''}`
                    : currentHlsLevel >= 0 && hlsLevels[currentHlsLevel]
                      ? `${hlsLevels[currentHlsLevel].height}p`
                      : 'Auto'}
              </button>
              {showQualityMenu && (
                <div className={`absolute bottom-full right-0 mb-2 py-1 min-w-[104px] z-20 ${MENU_SURFACE}`} role="menu">
                  {originalSrc && (
                    <button
                      onClick={switchToOriginal}
                      className={menuItemClass(isOriginalMode)}
                      role="menuitemradio"
                      aria-checked={isOriginalMode}
                    >
                      Original
                    </button>
                  )}
                  {hlsLevels
                    .map((level, idx) => ({ level, idx }))
                    .filter(({ level }) => level.name !== 'Original')
                    .sort((a, b) => b.level.bitrate - a.level.bitrate)
                    .map(({ level, idx }) => (
                      <button
                        key={idx}
                        onClick={() => changeQuality(idx)}
                        className={menuItemClass(!isAutoQuality && !isOriginalMode && currentHlsLevel === idx)}
                        role="menuitemradio"
                        aria-checked={!isAutoQuality && !isOriginalMode && currentHlsLevel === idx}
                      >
                        {level.height}p
                      </button>
                    ))}
                  <button
                    onClick={() => changeQuality(-1)}
                    className={menuItemClass(isAutoQuality && !isOriginalMode)}
                    role="menuitemradio"
                    aria-checked={isAutoQuality && !isOriginalMode}
                  >
                    Auto
                  </button>
                </div>
              )}
            </div>
          ) : resolution ? (
            <span className="px-2 py-1 text-xs font-mono text-ink-400 select-none">
              {resolution.height >= 2160 ? '4K' : resolution.height >= 1080 ? '1080p' : resolution.height >= 720 ? '720p' : `${resolution.height}p`}
            </span>
          ) : null}

          {/* Settings — opens shortcuts help */}
          {onToggleShortcuts && (
            <button
              onClick={onToggleShortcuts}
              className={`${BAR_BUTTON} py-1.5`}
              aria-label="Keyboard Shortcuts"
              title="Keyboard Shortcuts (?)"
            >
              <Settings size={16} />
            </button>
          )}

          {/* Fullscreen */}
          <button
            onClick={handleFullscreen}
            className="px-2 py-1.5 rounded text-white hover:bg-white/10 transition-colors"
            aria-label="Fullscreen"
            title="Fullscreen (f)"
          >
            <Maximize size={16} />
          </button>
        </div>
      </div>
    </div>
  );
};

export default VideoPlayer;
