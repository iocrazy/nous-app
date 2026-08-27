import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  AlertCircle, AlertTriangle, ArrowLeftRight, Bookmark, Calendar, Check, ChevronLeft,
  ChevronRight, Folder, Images, ListOrdered, Loader2, MapPin, Music, Pause, Play, Plus, Radio,
  RefreshCw, Search, Send, Sparkles, TrendingUp, X,
} from 'lucide-react';
import {
  createPublishTask, getPlatformCapabilities, listAccounts, listGeneratedVideos,
  listLibraryMedia, musicSearchFailure, promoteGeneratedVideo, publishGateProblems,
  fetchMusicCharts, musicChartKey, refreshMusicCharts,
  searchMusic, suggestTopics, topicSuggestReason, GeneratedVideo, MusicBrowseIdentity,
  MusicChart, MusicSearchReason, MusicTrack, PlatformCapability, PublishGateProblem,
  TopicSuggestion, TopicSuggestReason,
} from '../../services/distributionService';
import {
  uploadResource, getGalleryItems, getResourceCoverUrl, getResourceFileUrl, GALLERY_MIME,
  type ResourceUploadError, type UploadFailureReason,
} from '../../services/resourceService';
import { getSupabaseClient } from '../../supabaseClient';
import {
  addResourceTag, createTag, removeResourceTag,
} from '../../services/unifiedTagService';
import { TO_PUBLISH_TAG_NAME, findToPublishTagId } from '../../services/toPublishService';
import { AccountAvatar } from './platform';
import { needsReconnect } from './accountStatus';
import { SocialAccount, LibraryVideo, SelfDeclaration, TopicRef } from '../../types';
import { CoverPicker, CoverPair } from './CoverPicker';
import { CoverStudioOverlay } from './CoverStudio/CoverStudioOverlay';
import { PublishPreview, type PreviewSoundtrack } from './PublishPreview';
import { UiSelect } from '../ui/primitives';
import { DateTimePopover } from '../common/DateTimePopover';
import { useToast } from '../Toast';
import { useWorkspaceScope } from '../../hooks/useWorkspaceScope';
import { PageHeader } from '../layout/PageHeader';
import './distribution-v4.css';

type Visibility = 'public' | 'friends' | 'private';
type Mode = 'broadcast' | 'one_to_one';
/**
 * Publish routes, in the order the backend prefers them.
 *
 * The page always submits `'session'` and lets the backend pick per account —
 * `decide_channel` reads each account's `auth_type` and falls through:
 *   session account  -> browser automation (unattended)
 *   oauth account    -> H5 handoff (user confirms on their phone)
 * Asking the user to choose a channel would only let them pick one that cannot
 * work for the account they selected.
 */
type Channel = 'official' | 'h5' | 'session';
type Orientation = 'vertical' | 'horizontal';
type ContentKind = 'video' | 'images';

const VIS: Visibility[] = ['public', 'friends', 'private'];
const PLATFORM_LABEL: Record<string, string> = {
  douyin: 'Douyin', kuaishou: 'Kuaishou', xiaohongshu: 'Xiaohongshu',
};

// The "To Publish" well-known tag (name, lookup, lazy-create) now lives in
// services/toPublishService.ts so the Resources context-menu "Mark to publish"
// action and this picker filter share one source of truth.

// Mirrors the backend schema bounds (normalize_topics): ≤20 tags, ≤50 chars.
const MAX_TOPICS = 20;
const MAX_TOPIC_LEN = 50;
// Type-ahead debounce. Long enough that a normal typing burst produces one
// request instead of one per keystroke, short enough that the list feels like
// it belongs to the keyboard rather than to a spinner.
const TOPIC_SUGGEST_DEBOUNCE_MS = 250;
// Longer than the topic one, deliberately. A topic lookup is anonymous; a
// catalogue lookup mints a search credential with a REAL account's cookies,
// so every keystroke that turns into a request is charged to that account's
// risk profile. 450ms is roughly "the user stopped typing" rather than "the
// user paused mid-word".
const MUSIC_SEARCH_DEBOUNCE_MS = 450;

/**
 * Cumulative play count, written the way the reader's own locale writes large
 * numbers: `31B` in English, `309亿` in Chinese.
 *
 * Delegated to `Intl` on purpose. A hand-rolled 亿/万 table would be wrong in
 * English and a hand-rolled B/M table would be wrong in Chinese (the buckets
 * are 10^8/10^4 vs 10^9/10^6 — they do not line up), so the one thing we must
 * not do is pick one and translate the suffix.
 */
const formatViewCount = (n: number, lang: string): string => {
  try {
    return new Intl.NumberFormat(lang || 'en', {
      notation: 'compact',
      maximumFractionDigits: 1,
    }).format(n);
  } catch (err) {
    console.error('distribution: compact view count formatting failed', err);
    return String(n);
  }
};
// ── Picker metadata formatting ────────────────────────────────────────
//
// The publish picker used to show a thumbnail and a filename, nothing else.
// That is not enough to answer the only question the picker exists to answer
// when the library holds several cuts of the same piece: WHICH ONE IS THIS?
// Duration, resolution, size and date are what tell two versions apart.
//
// The shared rule for all four: **a missing value renders as an em dash, never
// as a guess.** No "0:00" for an unknown duration, no size back-computed from
// bitrate. An invented number here is worse than a blank one — the user would
// publish the wrong cut and never know why.

/** Em dash for "we do not have this value". One constant so the fallback is
 *  identical everywhere and greppable. */
const NO_VALUE = '—';

/**
 * The URL this track can actually be auditioned from, or null.
 *
 * Two separate reasons a row has no preview, and BOTH have to read as "no
 * preview" rather than as a control that does nothing:
 *
 *  - `play_url` is empty. The backend documents this as the norm, not the
 *    exception ("上游白送的，缺失是常态") — the upstream catalogue simply does
 *    not carry an audition file for every row.
 *  - `play_url` is `http://`. The backend's `_first_url` accepts anything
 *    starting with `http`, and app.nous.ink is https — a mixed-content URL is
 *    blocked by the browser BEFORE any error event we could show reaches us.
 *    A play button over one of those is the exact shape this repo keeps
 *    banning: a control that can only ever no-op.
 *
 * ⚠️ [Corrected 2026-08-17] An earlier note here claimed the CDN answered
 * "identically with and without a `Referer`". That probe used a stale URL out
 * of a captured fixture, and it had the conclusion exactly backwards. Against a
 * REAL `play_url` the platform's CDN is a *reverse* hotlink guard — it rejects
 * requests that CARRY a referrer:
 *
 *     Referer: https://app.nous.ink/  ->  HTTP 403 (150-byte error body)
 *     no Referer at all               ->  HTTP 206, audio (CORS: `*`)
 *
 * A browser loading media always sends one, so every audition was a guaranteed
 * 403. `fetchMusicPreview` below is the response; see its note for why the
 * obvious `referrerPolicy` attribute is not.
 */
export function musicPreviewUrl(track: { play_url?: string | null }): string | null {
  const raw = (track.play_url ?? '').trim();
  return raw.startsWith('https://') ? raw : null;
}

/**
 * Load an audition into a same-origin `blob:` URL, sending no referrer.
 *
 * ⚠️ Why not `<audio referrerPolicy="no-referrer">`, which is the obvious fix
 * and the one this change originally set out to make: **the browser ignores
 * it.** HTML defines the `referrerpolicy` content attribute on `a`, `area`,
 * `img`, `iframe`, `link` and `script` — media elements are not on that list.
 * React renders it as an unknown attribute, devtools shows it sitting on the
 * node, and the request goes out with the referrer anyway.
 *
 * [Measured 2026-08-17, headless Chrome against a server echoing what it got]
 *
 *     <audio src>                              -> Referer: <origin>
 *     <audio referrerpolicy="no-referrer">     -> Referer: <origin>   ← ignored
 *     <img   referrerpolicy="no-referrer">     -> absent   (control)
 *     fetch(url, {referrerPolicy:'no-referrer'}) -> absent
 *
 * The `<img>` row is a positive control: without it, "no suppression observed"
 * could equally mean the probe was blind, and the whole result would prove
 * nothing. It is the same reason a health probe has to be falsifiable.
 *
 * `fetch` is therefore the only per-request lever. The alternative — a
 * document-level `<meta name="referrer">` — would change referrer behaviour for
 * every request the app makes, which is a large blast radius for one button.
 *
 * The cost is that an audition downloads in full instead of streaming ranges,
 * so the caller must show that it is loading. `fetch` also needs real CORS
 * headers where a media element could have settled for an opaque response —
 * the CDN sends `access-control-allow-origin: *`, which is what makes this
 * possible at all.
 */
export async function fetchMusicPreview(
  url: string,
  fetcher: typeof fetch = fetch,
): Promise<string> {
  const response = await fetcher(url, {
    // The entire point. Not a default worth relying on: Chrome's default is
    // `strict-origin-when-cross-origin`, which still sends the origin — and the
    // origin is exactly what this CDN rejects.
    referrerPolicy: 'no-referrer',
    // Stated rather than left implicit: an opaque response would give us a blob
    // of zero length that plays as silence, which is a silent failure wearing
    // the shape of success.
    mode: 'cors',
    credentials: 'omit',
  });
  if (!response.ok) {
    throw new Error(`music preview responded ${response.status}`);
  }
  const blob = await response.blob();
  if (blob.size === 0) {
    // A zero-length body decodes to nothing and fires no `error` — the button
    // would look like it worked and produce silence.
    throw new Error('music preview was empty');
  }
  return URL.createObjectURL(blob);
}

/** `154` → `2:34`; `3616` → `1:00:16`. Null/negative → em dash. */
const formatDuration = (seconds: number | null | undefined): string => {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return NO_VALUE;
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
};

/**
 * `19364154` → `18.5 MB`; `550133695` → `525 MB`. Binary units (MiB semantics,
 * MB labels) to match what the OS file manager shows for the same file.
 *
 * The decimal is dropped at ≥100 because by then a tenth of a megabyte is
 * noise, not a signal that separates two cuts.
 */
const formatBytes = (bytes: number | null | undefined): string => {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return NO_VALUE;
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
};

/**
 * ══ HOW FAST IS THIS UPLOAD GOING ═══════════════════════════════════════════
 *
 * A rate is a DISPLAY decision, which is why it is computed here and not in
 * `resourceService`. The service reports what the browser told it plus when
 * (`UploadProgressSample`); everything below — how long a window to average
 * over, how much to smooth, when a reading is not a reading — is about what a
 * person can read off a screen without being misled.
 *
 * ⚠️ WHY SMOOTHED AT ALL, AND WHY NOT AN ETA.
 *
 * The browser delivers upload progress in bursts as socket buffers drain, so
 * the instantaneous rate between two adjacent samples swings by an order of
 * magnitude for reasons that have nothing to do with the connection. Printed
 * raw it is a number that changes every frame and means nothing — worse than
 * no number, because it looks like a measurement.
 *
 * A remaining-time figure is the same problem multiplied: it divides a
 * remainder by that same jittery rate, so it jumps around AND carries an
 * authority ("6 seconds") that the underlying reading cannot support. So there
 * is no ETA here. A stable rate lets the reader judge the wait themselves; a
 * flickering countdown just asserts a precision nobody has.
 */

/**
 * How far apart two samples must be before the pair counts as a reading.
 *
 * Under this, the denominator is small enough that ordinary burstiness
 * dominates the answer. Samples closer together than this are not discarded —
 * they are simply not folded in yet, and the next one far enough out measures
 * across the whole gap.
 */
export const UPLOAD_RATE_MIN_GAP_MS = 400;

/** Weight given to the newest reading. Lower = steadier, slower to react. */
export const UPLOAD_RATE_SMOOTHING = 0.3;

export interface UploadRateState {
  /**
   * Smoothed bytes per second, or null until a first real reading exists.
   *
   * Null is a state the UI must render as "not known yet", NOT as zero. Zero
   * is a measurement meaning "stalled"; before the first reading there is no
   * measurement at all, and the two look identical on screen if collapsed.
   */
  bytesPerSecond: number | null;
  /** The sample the next reading will be measured against. */
  last: { loaded: number; at: number } | null;
}

export const emptyUploadRate = (): UploadRateState => ({ bytesPerSecond: null, last: null });

/**
 * Fold one aggregate sample into the running estimate.
 *
 * Pure and exported so it can be tested as arithmetic rather than only through
 * a mocked XHR — the smoothing is the part most likely to be quietly wrong,
 * and it is invisible from the outside.
 */
export function foldUploadRate(
  state: UploadRateState,
  sample: { loaded: number; at: number },
): UploadRateState {
  if (state.last === null) {
    // First sample: an anchor, not yet a reading. One point has no rate.
    return { bytesPerSecond: state.bytesPerSecond, last: sample };
  }
  const gapMs = sample.at - state.last.at;
  if (gapMs < UPLOAD_RATE_MIN_GAP_MS) return state;

  const deltaBytes = sample.loaded - state.last.loaded;
  if (deltaBytes < 0) {
    /* The aggregate went backwards — a file dropped out of the batch, say.
       Re-anchor rather than fold a negative rate in, which would print a
       speed the connection never ran at. */
    return { bytesPerSecond: state.bytesPerSecond, last: sample };
  }

  const instant = (deltaBytes * 1000) / gapMs;
  return {
    bytesPerSecond: state.bytesPerSecond === null
      ? instant
      : state.bytesPerSecond + (instant - state.bytesPerSecond) * UPLOAD_RATE_SMOOTHING,
    last: sample,
  };
}

/**
 * One file that did not upload, in terms the UI can put a sentence to.
 *
 * `'unknown'` is deliberately its own case rather than being folded into
 * `'network'`: a throw we did not classify is not evidence of a dropped
 * connection, and saying so would be inventing a cause — the same defect as
 * inventing a number, in prose.
 */
export interface UploadFailureNote {
  name: string;
  reason: UploadFailureReason | 'unknown';
  /** The backend's own words, when it sent any. Never ours. */
  detail: string | null;
}

/**
 * Classify a rejected upload without guessing at anything it did not say.
 *
 * ⚠️ Recognises the service's error by its `name` and the shape of `reason`,
 * NOT with `instanceof`. Crossing a module boundary to compare constructor
 * identity is exactly the check that quietly stops holding when the other side
 * is a test double or a second copy of the module in a bundle — and it fails
 * by throwing from inside a `catch`, so the failure surfaces as the upload
 * handler blowing up rather than as a misclassified error. `name` is a stable,
 * declared discriminant; the reason is re-validated as a string because a
 * label alone is not a payload.
 *
 * Anything else is `'unknown'`, which is its own case for a reason: a throw we
 * did not classify is not evidence of a dropped connection, and calling it one
 * would be inventing a cause.
 */
export function describeUploadFailure(
  name: string,
  err: unknown,
): UploadFailureNote {
  const candidate = err as Partial<ResourceUploadError> | null;
  if (
    err instanceof Error
    && candidate?.name === 'ResourceUploadError'
    && typeof candidate.reason === 'string'
  ) {
    return {
      name,
      reason: candidate.reason,
      detail: typeof candidate.detail === 'string' ? candidate.detail : null,
    };
  }
  return { name, reason: 'unknown', detail: null };
}

/**
 * `"1080x2142"` → `"1080×2142"` (real multiplication sign).
 *
 * Deliberately NOT normalised to "1080p"-style shorthand: the picker's job is
 * telling near-identical files apart, and a vertical 1080×1920 and a landscape
 * 1920×1080 would collapse to the same label under that scheme.
 */
const formatResolution = (resolution: string | null | undefined): string => {
  if (!resolution) return NO_VALUE;
  return resolution.replace(/x/i, '×');
};

/** Locale-aware short date. Unparseable/absent → em dash. */
const formatShortDate = (iso: string | null | undefined, lang: string): string => {
  if (!iso) return NO_VALUE;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NO_VALUE;
  try {
    return new Intl.DateTimeFormat(lang || 'en', {
      year: 'numeric', month: 'short', day: 'numeric',
    }).format(d);
  } catch (err) {
    console.error('distribution: picker date formatting failed', err);
    return d.toISOString().slice(0, 10);
  }
};

// Cap concurrent image uploads so a large multi-select can't open dozens of
// parallel requests at once — pick order is preserved regardless of timing.
const UPLOAD_CONCURRENCY = 3;

/**
 * Douyin's 自主声明 (self declaration) — a compliance control on the creator
 * page, six fixed options.
 *
 * `value` is the platform's OWN wording, verbatim. It is what travels on the
 * wire and what gets stored, because the browser service selects the option by
 * matching that text in the DOM: a translated value would silently select
 * nothing and the post would go out undeclared. The English `label` is display
 * only (CLAUDE.md UI language rule) and never leaves the browser.
 *
 * Kept in sync with backend `services/distribution/publish_options.py`
 * (SELF_DECLARATIONS) — the backend rejects anything outside the six.
 */
const SELF_DECLARATIONS: Array<{ value: SelfDeclaration; key: string; label: string }> = [
  { value: '内容由AI生成', key: 'declAi', label: 'AI-generated content' },
  { value: '内容为个人观点或见解', key: 'declOpinion', label: 'Personal opinion or insight' },
  { value: '内容为转载信息', key: 'declRepost', label: 'Reposted information' },
  { value: '内容含营销推广信息', key: 'declMarketing', label: 'Contains marketing or promotion' },
  { value: '虚构演绎，仅供娱乐', key: 'declFiction', label: 'Fictional dramatization, entertainment only' },
  { value: '无需添加自主声明', key: 'declNone', label: 'No declaration needed' },
];
const DECLARATION_AI: SelfDeclaration = '内容由AI生成';

/**
 * Fallback schedule window, used only until `GET /distribution/capabilities`
 * answers (or when the platforms in play state no bound of their own).
 *
 * Douyin accepts a scheduled time between 2 hours and 14 days out — and the
 * time is typed into the creator page only AFTER the upload finishes, which
 * takes minutes. So the lead time is the platform's 2 hours plus a 10-minute
 * upload margin: a request in the 2h00–2h10 band would pass every check we make
 * and then be refused by Douyin after a few hundred MB went up. The backend
 * profile carries that same 7800s in `schedule_min_lead_seconds`, the request
 * schema re-checks it, and the browser service enforces it again
 * (SCHEDULE_LEAD_SLACK) — all of them must agree or the user gets
 * accepted-then-rejected.
 *
 * No margin on the upper bound: time passing only moves the target closer.
 */
const SCHEDULE_MIN_LEAD_MS = (2 * 60 + 10) * 60 * 1000;
const SCHEDULE_MAX_AHEAD_MS = 14 * 24 * 60 * 60 * 1000;

export type ScheduleProblem = 'empty' | 'tooSoon' | 'tooFar' | null;

/**
 * A local wall-clock value (`YYYY-MM-DDTHH:mm`, no offset) → the window verdict.
 * Exported so the boundaries are unit-testable without a DOM.
 *
 * The picker already refuses to *offer* anything outside the window, so this is
 * the second line rather than the first: it still catches `empty`, a value that
 * aged out of the window while the form sat open, and any future caller that
 * writes `scheduledAt` without going through the picker.
 */
export const scheduleProblem = (
  localValue: string,
  now: number = Date.now(),
  minLeadMs: number = SCHEDULE_MIN_LEAD_MS,
  maxAheadMs: number = SCHEDULE_MAX_AHEAD_MS,
): ScheduleProblem => {
  if (!localValue) return 'empty';
  const at = new Date(localValue).getTime();
  if (Number.isNaN(at)) return 'empty';
  const delta = at - now;
  if (delta < minLeadMs) return 'tooSoon';
  if (delta > maxAheadMs) return 'tooFar';
  return null;
};

/** Default seconds used when the capabilities response is not in yet. */
const DEFAULT_MIN_LEAD_S = SCHEDULE_MIN_LEAD_MS / 1000;
const DEFAULT_MAX_AHEAD_S = SCHEDULE_MAX_AHEAD_MS / 1000;

// Deterministic gradient pick per account id — keeps avatars visually
// distinct without needing per-user color config.
const AVA_GRADIENTS = [
  'linear-gradient(135deg,#0ea5e9,#6366f1)',
  'linear-gradient(135deg,#8b5cf6,#ec4899)',
  'linear-gradient(135deg,#f97316,#ef4444)',
  'linear-gradient(135deg,#10b981,#0ea5e9)',
  'linear-gradient(135deg,#f59e0b,#ec4899)',
];
const gradientFor = (id: string): string => {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return AVA_GRADIENTS[h % AVA_GRADIENTS.length];
};

const PLATFORM_BADGE: Record<string, { bg: string; icon: React.ReactNode }> = {
  douyin: {
    bg: '#000',
    icon: (
      <svg viewBox="0 0 24 24" fill="#fff">
        <path d="M16.6 5.82A4.28 4.28 0 0 1 15.54 3h-3.09v12.4a2.59 2.59 0 1 1-1.77-2.45V9.79a5.76 5.76 0 1 0 4.86 5.69V9.05a7.35 7.35 0 0 0 4.3 1.38V7.3a4.28 4.28 0 0 1-3.24-1.48Z" />
      </svg>
    ),
  },
  kuaishou: {
    bg: '#FF4906',
    icon: <svg viewBox="0 0 24 24" fill="#fff"><path d="m10 8 6 4-6 4Z" /></svg>,
  },
  xiaohongshu: {
    bg: '#FE2C55',
    icon: <svg viewBox="0 0 24 24" fill="#fff"><circle cx="12" cy="12" r="5" /></svg>,
  },
};

/* The heart / comment / share / music glyphs that used to live here went with
   the feed mock-up they decorated. Two of them carried a hardcoded `0`, which
   is a measurement the post does not have, and the music line asserted
   "Original sound" on a page that can attach a music track.

   They came back on the immersive preview's side rail under exactly the terms
   this note set: `aria-hidden`, no hit targets, and NO numbers beside them —
   see `immersiveOverlay` in PublishPreview.tsx. The music line came back too,
   but only as one of three explicitly distinguished cases; `previewSoundtrack`
   below is where that decision is made, and it is the caller's job precisely
   because the view has no way to tell "nothing picked" from "a keyword whose
   result nobody knows yet". */

export const PublishPage: React.FC = () => {
  const { t, i18n } = useTranslation();
  const { addToast } = useToast();
  const navigate = useNavigate();
  const { scopeId } = useWorkspaceScope();

  const [contentType, setContentType] = useState<ContentKind>('video');
  const [videos, setVideos] = useState<LibraryVideo[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  /**
   * Per-platform capabilities, straight from the backend. `null` = we have no
   * answer (still asking, or the request failed).
   *
   * Null must read as "supports nothing", never as "assume yes": an empty map
   * during the first paint would otherwise let the Images tab flash open and
   * arm a post the browser service is going to refuse. Failing closed costs a
   * disabled tab for a moment; failing open costs the exact late refusal this
   * whole endpoint exists to prevent.
   */
  const [capabilities, setCapabilities] = useState<Record<string, PlatformCapability> | null>(null);
  /**
   * How we came to be holding (or not holding) that map.
   *
   * The map alone cannot tell the three cases apart, and they are three
   * different sentences to a user: we have not asked yet, our own request
   * failed, or the platform genuinely answered "no". Collapsing them is how the
   * page ended up telling a user with a perfectly capable account that "the
   * connected platforms can only publish video" — an assertion about the
   * platform, made from no evidence at all, while the request was still in
   * flight.
   *
   * The safe default does NOT change with this state: every non-`ready` case
   * still leaves image posts disabled (see `imagesGate`). What changes is only
   * what we SAY, which is the part that was lying.
   */
  const [capState, setCapState] = useState<'loading' | 'ready' | 'error'>('loading');
  // selectedVideos holds media resource ids in PICK ORDER — for images that
  // order IS the gallery order sent to the note.
  const [selectedVideos, setSelectedVideos] = useState<string[]>([]);
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([]);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [topics, setTopics] = useState<string[]>([]);
  const [topicInput, setTopicInput] = useState('');
  const [topicInputOpen, setTopicInputOpen] = useState(false);
  /**
   * Entity bindings for the topics that came out of the suggestion dropdown,
   * keyed by lowercased name (the same key `addTopic` de-duplicates on).
   *
   * A hand-typed topic has no entry and that is fine — this map is a parallel
   * record, never the source of truth for what gets published. It exists
   * because the platform's `cid` is only observable at pick time.
   */
  const [topicRefs, setTopicRefs] = useState<Record<string, TopicRef>>({});
  const [suggestions, setSuggestions] = useState<TopicSuggestion[]>([]);
  /**
   * The dropdown has four distinct things to say and they must not collapse
   * into one another — an empty list rendered for a failed lookup is the exact
   * "silent no-op" this repo keeps getting burned by.
   */
  const [suggestState, setSuggestState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [suggestError, setSuggestError] = useState<TopicSuggestReason | 'unknown' | null>(null);
  // Monotonic request id — a response whose id is no longer the latest is a
  // stale answer to an older word and must never repaint the list.
  const suggestSeq = useRef(0);
  const [visibility, setVisibility] = useState<Visibility>('public');
  // The 3:4 / 4:3 pair derived from a video frame. Null = let the platform
  // pick its own frame at publish time.
  const [covers, setCovers] = useState<CoverPair | null>(null);
  const [coverStudioOpen, setCoverStudioOpen] = useState(false);
  const [aiContent, setAiContent] = useState(false);
  // '' = leave the platform's declaration control alone. Deliberately NOT the
  // same as '无需添加自主声明', which is a declaration the user chose and the
  // platform records.
  const [selfDeclaration, setSelfDeclaration] = useState<SelfDeclaration | ''>('');
  // Whether the current declaration was filled in BY the AI toggle rather than
  // by the user. Turning the toggle back off should undo what the toggle did —
  // and nothing else.
  const [declarationAuto, setDeclarationAuto] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<'now' | 'schedule'>('now');
  // Local wall clock, no offset ('YYYY-MM-DDTHH:mm') — converted to an absolute
  // ISO instant only at submit time. Kept in that shape after the native input
  // was replaced by DateTimePopover, which speaks it too.
  const [scheduledAt, setScheduledAt] = useState('');
  // The picker's anchor; non-null = open.
  const [scheduleAnchor, setScheduleAnchor] = useState<HTMLElement | null>(null);
  const [collectionName, setCollectionName] = useState('');
  // Douyin 「选择音乐」. Empty = leave the control alone, i.e. the platform
  // default (原声) — what every post published before this field existed got.
  // A name that the platform's own search cannot find fails that account's row
  // rather than publishing without music; see the backend's `music_name`.
  const [musicName, setMusicName] = useState('');
  /**
   * The track picked out of the platform's own catalogue, or null.
   *
   * `musicName` above stays as the search keyword the browser types into the
   * platform's dialog; THIS is the identity. A title is not one — one search
   * for 「起风了」 comes back with five character-identical titles under
   * different ids, and the publish step refuses (`music_ambiguous`) rather
   * than guess. Picking a card is what makes that refusal avoidable.
   */
  const [musicTrack, setMusicTrack] = useState<MusicTrack | null>(null);
  const [musicPanelOpen, setMusicPanelOpen] = useState(false);
  const [musicQuery, setMusicQuery] = useState('');
  const [musicResults, setMusicResults] = useState<MusicTrack[]>([]);
  const [musicState, setMusicState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [musicError, setMusicError] = useState<
    { reason: MusicSearchReason | 'unknown'; retryAfterS: number | null } | null
  >(null);
  /** Whose session the catalogue was read with — shown, never inferred. */
  const [musicIdentity, setMusicIdentity] = useState<MusicBrowseIdentity | null>(null);
  const musicSeq = useRef(0);
  /**
   * The platform's chart tabs, out of OUR cache.
   *
   * Reading a chart for real costs a browser run and leaves a draft on the
   * account (the panel only exists inside the platform's gallery editor, which
   * only exists after an upload), so this list is whatever the last harvest
   * stored — never a live fetch. `chartsMeta` carries the three freshness
   * facts the backend keeps apart, because "nothing has ever been read" and
   * "this is from yesterday" need different sentences and only one of them is
   * a warning.
   */
  const [musicCharts, setMusicCharts] = useState<MusicChart[]>([]);
  const [musicChartsMeta, setMusicChartsMeta] = useState<
    { stale: boolean; neverHarvested: boolean; lastSuccessAt: string | null } | null
  >(null);
  const [musicChartsState, setMusicChartsState] =
    useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  /** `kind:id` — BOTH halves. 推荐 and 收藏 share `category_id === '1'`. */
  const [activeChartKey, setActiveChartKey] = useState('');
  const [musicRefreshing, setMusicRefreshing] = useState(false);
  const musicChartsSeq = useRef(0);
  /**
   * Audition state. `musicPlayingId` is the ONE track sounding right now — a
   * single <audio> element enforces that structurally: starting a second row
   * cannot leave the first one playing, because there is no second element to
   * play it on. `musicPreviewFailed` holds the id whose playback we could not
   * start or could not load — pressing play and getting silence with no
   * explanation is exactly the silent no-op this repo forbids.
   */
  const [musicPlayingId, setMusicPlayingId] = useState<string | null>(null);
  const [musicPreviewFailed, setMusicPreviewFailed] = useState<string | null>(null);
  /**
   * The row whose audio is being fetched right now.
   *
   * Needed because the audition no longer starts at the speed of a `src`
   * assignment: the file is downloaded in full first (see `fetchMusicPreview`
   * for why it cannot stream). Without this the button would sit inert for the
   * length of a download and read as a dead control — the exact failure this
   * panel keeps being audited for.
   */
  const [musicPreviewLoading, setMusicPreviewLoading] = useState<string | null>(null);
  /**
   * `music_id` → the `blob:` URL its audio was downloaded into.
   *
   * Kept so re-pressing a row costs nothing, and — the reason it is a ref and
   * not state — so unmount can revoke every one of them. An object URL pins its
   * blob in memory for the life of the document; a panel that is searched a few
   * dozen times would otherwise hold every track it ever auditioned.
   */
  const musicPreviewBlobs = useRef<Map<string, string>>(new Map());
  /**
   * Which audition the user is currently waiting for. Compared on arrival so a
   * slow first download cannot start playing on top of a second row the user
   * pressed while it was in flight.
   */
  const musicPreviewSeq = useRef(0);
  /**
   * The <audio> node, kept in a ref that is NEVER written back to null.
   *
   * React detaches refs before passive-effect cleanups run, so a cleanup that
   * read `ref.current` on unmount would find null and pause nothing — the
   * element would go on sounding with its panel gone from the screen. Holding
   * the (possibly detached) node is harmless; pausing it is the entire point.
   */
  const musicAudioRef = useRef<HTMLAudioElement | null>(null);
  const attachMusicAudio = useCallback((el: HTMLAudioElement | null) => {
    if (el) musicAudioRef.current = el;
  }, []);
  /** Mirror of `musicPlayingId` readable from callbacks without re-binding them. */
  const musicPlayingIdRef = useRef<string | null>(null);

  /**
   * Take the spinner off ONE row.
   *
   * Guarded by id rather than written as `setMusicPreviewLoading(null)`: a
   * download that finishes after the user has already pressed a different row
   * would otherwise clear the new row's spinner, so the second press would
   * look idle while it was still fetching.
   */
  const clearPreviewLoadingFor = useCallback((trackId: string) => {
    setMusicPreviewLoading((current) => (current === trackId ? null : current));
  }, []);

  const stopMusicPreview = useCallback(() => {
    // A download in flight is also "the audition", so cancelling has to reach
    // it: bumping the ticket makes its arrival a no-op, and clearing the
    // spinner keeps the button from reading as busy forever.
    musicPreviewSeq.current += 1;
    setMusicPreviewLoading(null);
    // Nothing sounding, nothing to stop. The guard is not just tidiness: this
    // runs on mount and on every keystroke in the search box, and touching a
    // media element that never played is a call with no meaning behind it.
    if (musicPlayingIdRef.current === null) return;
    musicAudioRef.current?.pause();
    musicPlayingIdRef.current = null;
    setMusicPlayingId(null);
  }, []);

  /**
   * Start (or stop) the audition for a row.
   *
   * `play()` returns a promise that REJECTS on the browser's autoplay policy
   * and on a decode failure, and a rejected promise nobody handles is a click
   * that produces nothing at all — so it is caught and turned into the same
   * visible note the <audio> `error` event produces.
   */
  const toggleMusicPreview = useCallback(async (track: MusicTrack) => {
    const el = musicAudioRef.current;
    const url = musicPreviewUrl(track);
    if (!el || !url) return;
    if (musicPlayingIdRef.current === track.music_id) {
      stopMusicPreview();
      return;
    }
    el.pause();
    setMusicPreviewFailed(null);

    const ticket = ++musicPreviewSeq.current;
    // Downloaded once per track and remembered: pressing a row a second time
    // is instant, and the platform is not asked for the same object twice.
    let objectUrl = musicPreviewBlobs.current.get(track.music_id);
    if (!objectUrl) {
      setMusicPreviewLoading(track.music_id);
      try {
        objectUrl = await fetchMusicPreview(url);
        musicPreviewBlobs.current.set(track.music_id, objectUrl);
      } catch (err) {
        // 403 is the expected shape when the referrer suppression stops
        // working, and it must reach the user as a note on the row rather than
        // as a button that quietly does nothing.
        //
        // ⚠️ Reported WITHOUT consulting the ticket, unlike the playback path
        // below. A download can be cancelled by things the user did not do —
        // the results array is rebuilt whenever the debounced search re-runs,
        // and the effect that stops playback on new results bumps the ticket —
        // so gating this on the ticket meant a press could fail, say nothing,
        // and leave the spinner turning. The note is keyed by track id and is
        // cleared by that same effect, so a stale one cannot linger.
        console.error('distribution: music preview could not be fetched', err);
        clearPreviewLoadingFor(track.music_id);
        setMusicPreviewFailed(track.music_id);
        return;
      }
      if (musicPreviewSeq.current !== ticket) {
        // A different row was pressed while this one was downloading. The blob
        // is kept (it is cached above, and revoked on unmount) but it must not
        // start sounding on top of whatever the user asked for since.
        clearPreviewLoadingFor(track.music_id);
        return;
      }
      clearPreviewLoadingFor(track.music_id);
    }

    if (el.src !== objectUrl) el.src = objectUrl;
    try {
      el.currentTime = 0;
    } catch (err) {
      // Seeking before any metadata exists throws in some engines; the fresh
      // `src` above already starts at zero, so this is not worth failing on.
      console.warn('distribution: could not rewind music preview', err);
    }
    musicPlayingIdRef.current = track.music_id;
    setMusicPlayingId(track.music_id);
    const started = el.play();
    if (started && typeof started.catch === 'function') {
      started.catch((err: unknown) => {
        console.error('distribution: music preview failed to start', err);
        if (musicPlayingIdRef.current !== track.music_id) return;
        musicPlayingIdRef.current = null;
        setMusicPlayingId(null);
        setMusicPreviewFailed(track.music_id);
      });
    }
  }, [stopMusicPreview, clearPreviewLoadingFor]);

  /**
   * Sound whose source the user can no longer see is sound the user cannot
   * stop. Closing the panel and retyping the search both take away the row
   * whose play button is the only control over it, so both end the audition.
   * Unmount is covered by the cleanup below, which is why the element ref
   * above is never nulled.
   */
  useEffect(() => {
    stopMusicPreview();
    setMusicPreviewFailed(null);
  }, [musicPanelOpen, musicQuery, stopMusicPreview]);

  /**
   * A new result set ends the audition only if the row it belongs to is
   * actually gone.
   *
   * ⚠️ This used to hang off `musicResults` identity, and that was wrong in a
   * way that only showed up as flakiness. The search effect rebuilds the array
   * whenever it re-runs — which it does for reasons the user did not cause —
   * and the rebuilt array is a new object even when it holds the same rows. So
   * a press could have its download cancelled mid-flight by a refresh that
   * changed nothing on screen, and the button would simply never do anything.
   *
   * Asking whether the row is still listed states the actual rule ("the
   * control over this sound is gone") instead of using array identity as a
   * proxy for it, and a refresh that returns the same rows now leaves the
   * audition alone.
   */
  useEffect(() => {
    const sounding = musicPlayingIdRef.current;
    if (sounding === null) return;
    if (musicResults.some((track) => track.music_id === sounding)) return;
    stopMusicPreview();
    setMusicPreviewFailed(null);
  }, [musicResults, stopMusicPreview]);

  useEffect(() => () => {
    if (musicPlayingIdRef.current !== null) musicAudioRef.current?.pause();
    // Every object URL pins its blob for the life of the DOCUMENT, not of this
    // component — so a panel that was searched and auditioned a few dozen times
    // would keep every track it ever played until a full page load. Read off
    // the ref rather than from state for the same reason the element ref is
    // never nulled: refs are detached before this runs.
    for (const objectUrl of musicPreviewBlobs.current.values()) {
      URL.revokeObjectURL(objectUrl);
    }
    musicPreviewBlobs.current.clear();
  }, []);

  const [allowDownload, setAllowDownload] = useState(true);
  const [mode, setMode] = useState<Mode>('broadcast');
  // Not user-selectable: the backend routes per account (see the Channel type).
  const [channel] = useState<Channel>('session');
  const [orientation, setOrientation] = useState<Orientation>('vertical');
  const [customizeOpen, setCustomizeOpen] = useState<Record<string, boolean>>({});
  const [accountConfigs, setAccountConfigs] = useState<Record<string, { title: string }>>({});
  const [submitting, setSubmitting] = useState(false);
  /**
   * Typed reasons the last submit was refused (backend `GateProblem[]`, HTTP
   * 422). Cleared at the start of every attempt so the panel always describes
   * the current one.
   */
  const [gateProblems, setGateProblems] = useState<PublishGateProblem[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerQuery, setPickerQuery] = useState('');
  const [pickerTab, setPickerTab] = useState<'library' | 'generated'>('library');
  const [toPublishOnly, setToPublishOnly] = useState(false);
  /**
   * The video being previewed inside the picker, or null.
   *
   * Metadata narrows the choice; playback settles it. Two exports of the same
   * cut can match on duration, resolution and size and still differ in the
   * only way that matters (wrong take, wrong grade, missing subtitles), so the
   * picker has to be able to actually show the video — not just describe it.
   *
   * Kept as an id rather than a boolean so opening one preview closes any
   * other: exactly one <video> element is ever mounted, which is what stops a
   * grid of autoplaying decoders from being possible at all.
   */
  const [previewId, setPreviewId] = useState<string | null>(null);
  /**
   * Supabase JWT for `<video src>`. A media element cannot send an
   * Authorization header, so the file endpoint takes the token as `?token=`
   * (same transport CoverPicker uses for its frame `<img>`s). Unsigned is the
   * fallback, not the plan.
   */
  const [mediaToken, setMediaToken] = useState<string | undefined>(undefined);
  const [generated, setGenerated] = useState<GeneratedVideo[]>([]);
  // genId → promoted resource id (seeded from the backlink, extended on pick).
  const [genResourceIds, setGenResourceIds] = useState<Record<string, string>>({});
  const [toPublishTagId, setToPublishTagId] = useState<string | null>(null);
  const [markedIds, setMarkedIds] = useState<Set<string>>(new Set());
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  /**
   * The batch in flight, or null when nothing is uploading.
   *
   * ⚠️ `uploadResource` has reported progress since it was written; this page
   * simply never passed the callback. That is the fourth instance of the same
   * shape in this module alone — a capability that exists, a caller that does
   * not use it, and no error anywhere to say so. The reason the user saw a
   * spinner with no numbers behind it was not a missing feature.
   */
  const [uploadBatch, setUploadBatch] = useState<{
    fileCount: number;
    /** Sum of the batch's file sizes: the denominator, known up front. */
    totalBytes: number;
    /** Sum of bytes reported so far, capped per file at that file's size. */
    loadedBytes: number;
    rate: UploadRateState;
  } | null>(null);
  /**
   * Bytes reported per file, indexed by pick order.
   *
   * A ref rather than state because every one of the batch's uploads writes to
   * it concurrently: reading a sum out of React state inside a progress
   * handler would read a snapshot that its siblings have already moved past,
   * and the aggregate would sag by whatever landed in between.
   */
  const uploadLoadedRef = useRef<number[]>([]);
  /** Live for the duration of a batch; `abort()` really stops the requests. */
  const uploadAbortRef = useRef<AbortController | null>(null);
  /**
   * Files the last batch could not upload, with a typed reason each.
   *
   * Kept on screen rather than announced once in a toast: the thing this
   * replaces said "{{n}} image(s) failed to upload", which names no file and
   * no cause, so a user whose 500 MB shot was rejected for being 500 MB got
   * the same sentence as one whose connection dropped.
   */
  const [uploadFailures, setUploadFailures] = useState<UploadFailureNote[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Gallery cards expand into their child images on pick. Cache the ordered
  // child ids per gallery so the toggle-off path can remove the exact group
  // without re-fetching. `expandingGalleryId` drives the card busy state.
  const [galleryChildren, setGalleryChildren] = useState<Record<string, string[]>>({});
  const [expandingGalleryId, setExpandingGalleryId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const mediaType = contentType === 'images' ? 'image' : 'video';
    try {
      // Generated media is video-only today — skip it entirely in images mode.
      const [v, a, gen] = await Promise.all([
        listLibraryMedia(scopeId, { mediaType }),
        listAccounts(),
        contentType === 'images' ? Promise.resolve([]) : listGeneratedVideos(),
      ]);
      setVideos(v);
      setAccounts(a);
      setGenerated(gen);
      setGenResourceIds(Object.fromEntries(
        gen.filter((g) => g.promoted_resource_id).map((g) => [g.id, g.promoted_resource_id as string]),
      ));
    } catch (err) {
      console.error('distribution: publish page load failed', err);
      addToast(t('distribution.publish.loadFailed', 'Failed to load publish data'), 'error');
    }
    // "To publish" mark state — non-fatal side channel; failures leave the
    // filter empty but never block the page. Scoped to the current media type
    // so marks line up with the listed media.
    try {
      const tagId = await findToPublishTagId();
      if (tagId) {
        setToPublishTagId(tagId);
        const marked = await listLibraryMedia(scopeId, { tagId, mediaType });
        setMarkedIds(new Set(marked.map((m) => m.id)));
      }
    } catch (err) {
      console.error('distribution: load to-publish marks failed', err);
    }
  }, [addToast, t, scopeId, contentType]);

  useEffect(() => { void load(); }, [load]);

  /**
   * Capabilities are fetched on mount, not inside `load()`: they do not depend
   * on scope or content type, and re-fetching a table of constants every time
   * the user flips a tab is noise.
   *
   * A failure leaves `capabilities` at null — i.e. image posts stay disabled.
   * That is the safe direction and it is deliberate: a failed request is no
   * evidence that the platform supports galleries. It is also, on its own, no
   * evidence that the platform DOESN'T — hence `capState`, and hence this being
   * a callable rather than an inline effect: the user can ask again.
   *
   * Guarded by a sequence number (same pattern as `suggestSeq`) so a slow first
   * attempt landing after a retry cannot repaint the page with the older answer.
   */
  const capSeq = useRef(0);
  const loadCapabilities = useCallback(async () => {
    const seq = ++capSeq.current;
    setCapState('loading');
    try {
      const caps = await getPlatformCapabilities();
      if (seq !== capSeq.current) return;
      setCapabilities(caps);
      setCapState('ready');
    } catch (err) {
      if (seq !== capSeq.current) return;
      console.error('distribution: load platform capabilities failed', err);
      setCapState('error');
    }
  }, []);

  useEffect(() => { void loadCapabilities(); }, [loadCapabilities]);

  /**
   * A refusal describes the exact request that was refused. As soon as any
   * part of that request changes, the panel is blaming something that no
   * longer exists — and a stale "this was refused" next to a fixed form is
   * worse than no message at all.
   */
  useEffect(() => {
    setGateProblems([]);
  }, [contentType, selectedVideos, selectedAccounts, title]);

  // Escape closes the preview first, the picker second. Backing out of a
  // preview should not also discard the search and scroll position the user
  // built up to find it.
  useEffect(() => {
    if (!pickerOpen) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (previewId) setPreviewId(null);
      else setPickerOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [pickerOpen, previewId]);

  // Closing the picker must tear the <video> down. Without this the element
  // stays mounted in a hidden tree and keeps buffering — the user closed the
  // dialog, so the download should stop too.
  useEffect(() => {
    if (!pickerOpen) setPreviewId(null);
  }, [pickerOpen]);

  // Read the session token once for `<video src>` (see `mediaToken`).
  useEffect(() => {
    let alive = true;
    const supabase = getSupabaseClient();
    if (!supabase) return undefined;
    void supabase.auth.getSession()
      .then(({ data }) => { if (alive) setMediaToken(data?.session?.access_token); })
      .catch((err) => console.error('distribution: read session for preview failed', err));
    return () => { alive = false; };
  }, []);

  const toggle = (list: string[], id: string): string[] =>
    (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  const removeVideo = (id: string) =>
    setSelectedVideos((s) => s.filter((x) => x !== id));

  /**
   * Move one image earlier (-1) or later (+1) in the gallery.
   *
   * Keyed by id, not by the index of the rendered strip: `selectedVideos` is
   * what publishes, and a picked id whose Library row didn't resolve is
   * skipped when rendering. Swapping by rendered index would then move a
   * different image than the one the user clicked.
   */
  const moveImage = (id: string, delta: -1 | 1) =>
    setSelectedVideos((s) => {
      const i = s.indexOf(id);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= s.length) return s;
      const next = [...s];
      next[i] = s[j];
      next[j] = s[i];
      return next;
    });

  const isImages = contentType === 'images';

  /** The accounts this post actually reaches — all connected ones until the
   *  user narrows it down. Every capability question below is asked about
   *  exactly this set. */
  const targetAccounts = useMemo(
    () => (selectedAccounts.length
      ? accounts.filter((a) => selectedAccounts.includes(a.id))
      : accounts),
    [accounts, selectedAccounts],
  );

  /**
   * Whether we actually hold an answer about every account this post reaches.
   *
   * A response that carries no record for a platform is not a response that
   * said "no" — the old `?? false` flattened those two into a refusal, exactly
   * like a pending or failed request did.
   */
  const capsResolved = useMemo(
    () => capState === 'ready'
      && targetAccounts.every((a) => Boolean(capabilities?.[a.platform])),
    [capState, targetAccounts, capabilities],
  );

  /**
   * Why the Images tab is dead, or null when it is alive. FOUR reasons, and the
   * last three used to be one.
   *
   * `unsupported` is the only one that says anything about the platform, and it
   * is now the only one reachable with an actual answer in hand: `capState ===
   * 'ready'` plus a capability record for every target platform. Everything
   * else — request in flight, request failed, a response that simply has no
   * entry for this platform — is *no evidence*, and no evidence is not a "no".
   * It used to be reported as one ("the connected platforms can only publish
   * video"), which is how a user with a gallery-capable account got told their
   * platform could not do galleries.
   *
   * The gate value still closes the tab in every non-null case: the safe
   * default is untouched and must stay untouched (see `getPlatformCapabilities`
   * — a rejection means OUR call failed, never that the platform said no). This
   * split changes what we tell the user, not what we let them arm.
   *
   * `noAccounts` is tested first because it is answerable without capabilities
   * at all: with nothing connected, no capability response changes what the
   * user has to do next. It kept its own sentence from the previous round of
   * this same bug — "you have not connected an account" and "the platform you
   * connected cannot do this" used to share one, which told a user with no
   * accounts to go wait for a feature.
   *
   * Everything is asked about the accounts the post actually REACHES rather
   * than a global flag: "nothing can do this" and "the ones YOU picked can't"
   * are the same failure for the user. And nothing here needs editing when the
   * browser service learns galleries — the tab un-greys itself off the
   * response.
   */
  const imagesGate = useMemo<'noAccounts' | 'loading' | 'unknown' | 'unsupported' | null>(() => {
    if (targetAccounts.length === 0) return 'noAccounts';
    if (capState === 'loading') return 'loading';
    if (!capsResolved) return 'unknown';
    const ok = targetAccounts.every(
      (a) => capabilities?.[a.platform]?.content_types.includes('images') ?? false,
    );
    return ok ? null : 'unsupported';
  }, [targetAccounts, capabilities, capState, capsResolved]);

  const imagesSupported = imagesGate === null;

  /**
   * Whether every account this post reaches has a music picker we can drive.
   *
   * Read from the capabilities response, never decided here — same rule as the
   * image limits above. Without an answer (still loading, or the request
   * failed) the field stays hidden rather than collecting a name that would be
   * refused at submit with `music_not_supported` — but see
   * `capabilitiesUnavailable` below the row: a field that vanishes for want of
   * an answer has to say so, otherwise "we could not ask" is indistinguishable
   * from "your platform has no music picker".
   */
  const musicSupported = useMemo(
    () => targetAccounts.length > 0
      && targetAccounts.every((a) => capabilities?.[a.platform]?.supports_music ?? false),
    [targetAccounts, capabilities],
  );

  // Used as both placeholder and aria-label, so it has to be one value.
  const pickerSearchLabel = isImages
    ? t('distribution.publish.pickerSearchImages', 'Search images')
    : t('distribution.publish.pickerSearch', 'Search videos');

  /**
   * Says why the tab is dead, in the same words on the tooltip and in the card.
   *
   * One sentence per gate value, deliberately: the whole point of splitting the
   * gate is that these read differently. Only the `unsupported` line is allowed
   * to make a claim about the platform.
   */
  const imagesGateHint = ((): string | undefined => {
    switch (imagesGate) {
      case 'noAccounts':
        return t(
          'distribution.publish.noAccountsForImages',
          'Connect an account before publishing an image post.',
        );
      case 'loading':
        return t(
          'distribution.publish.capabilitiesLoading',
          'Checking what the connected platforms can publish…',
        );
      case 'unknown':
        return t(
          'distribution.publish.capabilitiesUnknown',
          'We could not read what the connected platforms can publish, so image posts stay off for now. This is our lookup failing, not the platform saying no.',
        );
      case 'unsupported':
        return t(
          'distribution.publish.imagesUnsupported',
          'Image posts are not supported yet — the connected platforms can only publish video.',
        );
      default:
        return undefined;
    }
  })();

  /**
   * We have accounts but no capability answer about them. Drives the places
   * that hide a control outright — hiding it is the right safe default, hiding
   * it *without saying so* is the silent no-op.
   */
  const capabilitiesUnavailable = targetAccounts.length > 0 && !capsResolved;

  /**
   * How many images one post may carry, for the accounts it reaches.
   *
   * Read from the capabilities response, never written here. Douyin's page
   * says "最多支持上传35张图片", the backend profile carries 35, the request
   * schema carries a neutral 35 — a fourth copy in this file is exactly the
   * kind of hand-synced declaration this whole project exists to delete. A
   * platform that doesn't state a bound contributes nothing (null), so the
   * strictest stated bound wins: `min` of the maxima, `max` of the minima.
   */
  const imageLimits = useMemo<{ min: number | null; max: number | null }>(() => {
    const caps = targetAccounts
      .map((a) => capabilities?.[a.platform])
      .filter((c): c is PlatformCapability => Boolean(c));
    const maxes = caps
      .map((c) => c.max_images)
      .filter((n): n is number => typeof n === 'number');
    const mins = caps
      .map((c) => c.min_images)
      .filter((n): n is number => typeof n === 'number');
    return {
      min: mins.length ? Math.max(...mins) : null,
      max: maxes.length ? Math.min(...maxes) : null,
    };
  }, [targetAccounts, capabilities]);

  /**
   * Whether the current gallery is outside those bounds. Blocking here is the
   * same fail-fast the backend gate does at submit time — the difference is
   * the user can still see which images they picked.
   */
  const imageCountProblem = useMemo<'tooMany' | 'tooFew' | null>(() => {
    if (!isImages || selectedVideos.length === 0) return null;
    if (imageLimits.max !== null && selectedVideos.length > imageLimits.max) return 'tooMany';
    if (imageLimits.min !== null && selectedVideos.length < imageLimits.min) return 'tooFew';
    return null;
  }, [isImages, selectedVideos, imageLimits]);

  // Switch content type: clears the selection (video ids ≠ image ids), resets
  // the picker to the Library tab (Generated is video-only), and pins images
  // to broadcast (a note is one post per account — never round-robin split).
  const onContentTypeChange = (kind: ContentKind) => {
    if (kind === contentType) return;
    // The tab is already disabled — this is the second lock. A `disabled`
    // attribute is a rendering detail; the state change is what would arm a
    // post the platform is going to refuse.
    if (kind === 'images' && !imagesSupported) return;
    setContentType(kind);
    setSelectedVideos([]);
    // Covers are frames OF the cleared selection — keeping them would publish
    // a cover from content that is no longer in the post.
    setCovers(null);
    setPickerTab('library');
    if (kind === 'images') setMode('broadcast');
  };

  // ── Topics (Douyin hashtags) ──
  // Add one bare tag (strip leading '#'), enforcing the same bounds as the
  // backend schema and de-duplicating case-insensitively. Immutable update.
  const addTopic = useCallback((raw: string) => {
    const tag = raw.replace(/^#+/, '').trim();
    if (!tag || tag.length > MAX_TOPIC_LEN) return;
    setTopics((prev) => (
      prev.length >= MAX_TOPICS || prev.some((x) => x.toLowerCase() === tag.toLowerCase())
        ? prev
        : [...prev, tag]
    ));
  }, []);

  // Commit the input: split on comma/whitespace so a pasted "a, b c" adds all.
  const commitTopicInput = useCallback(() => {
    topicInput
      .split(/[,\s]+/)
      .map((s) => s.replace(/^#+/, '').trim())
      .filter(Boolean)
      .forEach((p) => addTopic(p));
    setTopicInput('');
  }, [topicInput, addTopic]);

  const removeTopic = (tag: string) =>
    setTopics((prev) => prev.filter((x) => x !== tag));

  const onTopicKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // IME composition guard (#1453 pattern): never commit mid-composition.
    if (e.nativeEvent.isComposing) return;
    if (e.key === 'Enter' || e.key === ',' || (e.key === ' ' && topicInput.trim())) {
      e.preventDefault();
      commitTopicInput();
    } else if (e.key === 'Backspace' && !topicInput && topics.length) {
      e.preventDefault();
      setTopics((prev) => prev.slice(0, -1));
    }
  };

  /**
   * Take one suggestion: add it like any other topic AND remember the platform
   * entity it came from. The id is only knowable here — the same word typed by
   * hand carries no binding — so this is the single place it can be captured.
   */
  const pickSuggestion = useCallback((s: TopicSuggestion) => {
    addTopic(s.name);
    if (s.topic_id) {
      setTopicRefs((prev) => ({
        ...prev,
        [s.name.toLowerCase()]: {
          name: s.name,
          topic_id: s.topic_id,
          view_count: s.view_count,
        },
      }));
    }
    setTopicInput('');
    setSuggestions([]);
    setSuggestState('idle');
    setSuggestError(null);
  }, [addTopic]);

  /**
   * Type-ahead against the platform's own topic library, debounced.
   *
   * Two things this deliberately does NOT do:
   *  - it never turns a failed lookup into an empty list (the dropdown shows a
   *    typed error line instead — "nothing found" and "we could not ask" are
   *    different sentences);
   *  - it never lets a slow response overwrite a newer one: every run bumps a
   *    sequence number and a stale resolution is dropped on the floor.
   */
  useEffect(() => {
    const term = topicInput.replace(/^#+/, '').trim();
    if (!topicInputOpen || !term) {
      setSuggestions([]);
      setSuggestState('idle');
      setSuggestError(null);
      return;
    }
    const seq = ++suggestSeq.current;
    setSuggestState('loading');
    setSuggestError(null);
    const timer = window.setTimeout(() => {
      suggestTopics(term)
        .then((rows) => {
          if (seq !== suggestSeq.current) return;
          setSuggestions(rows);
          setSuggestState('ready');
        })
        .catch((err) => {
          if (seq !== suggestSeq.current) return;
          console.error('distribution: topic suggest failed', err);
          setSuggestions([]);
          setSuggestError(topicSuggestReason(err) ?? 'unknown');
          setSuggestState('error');
        });
    }, TOPIC_SUGGEST_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [topicInput, topicInputOpen]);

  /**
   * The entity bindings that belong to the topics actually being published,
   * in chip order. Deriving it here (instead of pruning `topicRefs` on every
   * removal) means the two lists cannot drift apart.
   */
  const pickedTopicRefs: TopicRef[] = useMemo(
    () => topics
      .map((tag) => topicRefs[tag.toLowerCase()])
      .filter((ref): ref is TopicRef => Boolean(ref)),
    [topics, topicRefs],
  );

  /** Human sentence for a typed lookup failure. Branches on the code, never
   *  on the backend's English prose. */
  const suggestErrorText = (): string => {
    if (suggestError === 'platform_unsupported') {
      return t(
        'distribution.publish.topicSuggestUnsupported',
        'This platform has no topic library we can search.',
      );
    }
    return t(
      'distribution.publish.topicSuggestFailed',
      'Could not reach the topic list — your typed topic still works.',
    );
  };

  /**
   * Search the platform's music catalogue, debounced.
   *
   * Three things this deliberately does NOT do, each one a failure this repo
   * has already paid for:
   *  - it never runs unless the panel is open. Every lookup mints a search
   *    credential with a real account's cookies, and a request the user did
   *    not ask for is a risk-control signal we spent for nothing;
   *  - it never turns a failed lookup into an empty list. A nonsense keyword
   *    still returns fuzzy matches upstream, so an empty panel is far more
   *    likely to mean "we failed" than "no such track";
   *  - it never lets a slow response overwrite a newer one.
   */
  useEffect(() => {
    const term = musicQuery.trim();
    const browseAccount = targetAccounts[0];
    if (!musicPanelOpen || !term || !browseAccount) {
      setMusicResults([]);
      setMusicState('idle');
      setMusicError(null);
      return;
    }
    const seq = ++musicSeq.current;
    setMusicState('loading');
    setMusicError(null);
    const timer = window.setTimeout(() => {
      searchMusic(term, browseAccount.id)
        .then((page) => {
          if (seq !== musicSeq.current) return;
          setMusicResults(page.tracks);
          setMusicIdentity(page.browsing_as);
          setMusicState('ready');
        })
        .catch((err) => {
          if (seq !== musicSeq.current) return;
          console.error('distribution: music search failed', err);
          const failure = musicSearchFailure(err);
          setMusicResults([]);
          setMusicError({
            reason: failure.reason ?? 'unknown',
            retryAfterS: failure.retryAfterS,
          });
          setMusicState('error');
        });
    }, MUSIC_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [musicQuery, musicPanelOpen, targetAccounts]);

  /**
   * The cached charts. Cheap — reads our own table, opens no browser — so
   * unlike the search above this needs no debounce and no keyword.
   *
   * Keyed on the browse account: 收藏 is account-scoped on the platform and
   * 推荐 is personalised, so switching target accounts really does change this
   * list. That is also why the panel names whose library it is showing.
   */
  useEffect(() => {
    const browseAccount = targetAccounts[0];
    if (!musicPanelOpen || !browseAccount) return;
    const seq = ++musicChartsSeq.current;
    setMusicChartsState('loading');
    fetchMusicCharts(browseAccount.id)
      .then((page) => {
        if (seq !== musicChartsSeq.current) return;
        setMusicCharts(page.charts);
        setMusicChartsMeta({
          stale: page.stale,
          neverHarvested: page.never_harvested,
          lastSuccessAt: page.last_success_at,
        });
        // Only default the tab when nothing is chosen: re-running this effect
        // (a refresh, a re-open) must not yank the user back to the first tab.
        setActiveChartKey((current) =>
          current || (page.charts[0] ? musicChartKey(page.charts[0]) : ''),
        );
        setMusicChartsState('ready');
      })
      .catch((err) => {
        if (seq !== musicChartsSeq.current) return;
        // Not silent: an unreadable cache must not look like a platform with
        // no charts (CLAUDE.md「触发路径必须类型化失败回显」).
        console.error('distribution: music charts failed', err);
        setMusicChartsState('error');
      });
  }, [musicPanelOpen, targetAccounts]);

  /** Harvest now. Expensive and it leaves a draft — the copy says so. */
  const runMusicChartRefresh = async () => {
    const browseAccount = targetAccounts[0];
    if (!browseAccount || musicRefreshing) return;
    setMusicRefreshing(true);
    try {
      await refreshMusicCharts(browseAccount.id);
      const page = await fetchMusicCharts(browseAccount.id);
      setMusicCharts(page.charts);
      setMusicChartsMeta({
        stale: page.stale,
        neverHarvested: page.never_harvested,
        lastSuccessAt: page.last_success_at,
      });
      setActiveChartKey((current) =>
        current || (page.charts[0] ? musicChartKey(page.charts[0]) : ''),
      );
      setMusicChartsState('ready');
    } catch (err) {
      console.error('distribution: music chart refresh failed', err);
      setMusicChartsState('error');
    } finally {
      setMusicRefreshing(false);
    }
  };

  /** The chart the tab row is pointing at, or null. */
  const activeChart = musicCharts.find((c) => musicChartKey(c) === activeChartKey) ?? null;

  /**
   * What the list shows. **A typed keyword wins.**
   *
   * The two sources are never merged: a search answers "songs called this",
   * a chart answers "what the platform is pushing today", and a list stitched
   * from both would be neither — while every row still looked legitimate.
   * Emptying the box returns to the chart rather than to a blank panel, which
   * is the whole reason the tabs exist.
   */
  const visibleTracks: MusicTrack[] = musicQuery.trim()
    ? musicResults
    : (activeChart?.tracks ?? []);

  /** Human sentence for a typed catalogue failure. Branches on the code,
   *  never on the backend's English prose. */
  const musicErrorText = (): string => {
    switch (musicError?.reason) {
      case 'session_unusable':
        return t(
          'distribution.publish.musicSessionUnusable',
          'This account needs reconnecting before its music library can be read.',
        );
      case 'account_not_session_bound':
        return t(
          'distribution.publish.musicAccountNotSession',
          'Only accounts connected by QR code can browse the music library.',
        );
      case 'signature_unavailable':
        // Distinguished from a dead upstream: this one recovers on its own,
        // and saying "broken" would send the user off to fix nothing.
        return t(
          'distribution.publish.musicBackingOff',
          'Music search is briefly unavailable after a failed handshake — try again in a moment.',
        );
      case 'platform_unsupported':
        return t(
          'distribution.publish.musicUnsupported',
          'This platform has no music library we can search.',
        );
      default:
        return t(
          'distribution.publish.musicSearchFailed',
          'Could not reach the music library — nothing was searched.',
        );
    }
  };

  // Only the videos the user actually picked are shown as content thumbs —
  // never the whole Library. Resolve ids → video rows, dropping any that no
  // longer exist in the loaded Library list.
  const selectedVideoObjs = useMemo(
    () => selectedVideos
      .map((id) => videos.find((v) => v.id === id))
      .filter((v): v is LibraryVideo => Boolean(v)),
    [selectedVideos, videos],
  );

  const pickerResults = useMemo(() => {
    const q = pickerQuery.trim().toLowerCase();
    const base = toPublishOnly ? videos.filter((v) => markedIds.has(v.id)) : videos;
    return q ? base.filter((v) => v.filename.toLowerCase().includes(q)) : base;
  }, [videos, pickerQuery, toPublishOnly, markedIds]);

  const generatedResults = useMemo(() => {
    const q = pickerQuery.trim().toLowerCase();
    return q ? generated.filter((g) => g.name.toLowerCase().includes(q)) : generated;
  }, [generated, pickerQuery]);

  // Toggle the "To Publish" mark on a Library video (creates the well-known
  // tag on first use). Optimistic; reverts by reloading marks on failure.
  const onToggleMark = async (videoId: string) => {
    const wasMarked = markedIds.has(videoId);
    setMarkedIds((prev) => {
      const next = new Set(prev);
      if (wasMarked) next.delete(videoId); else next.add(videoId);
      return next;
    });
    try {
      let tagId = toPublishTagId;
      if (!tagId) {
        const created = await createTag({ name: TO_PUBLISH_TAG_NAME, color: '#6366f1' });
        tagId = created.id;
        setToPublishTagId(tagId);
      }
      if (wasMarked) await removeResourceTag(videoId, tagId);
      else await addResourceTag(videoId, tagId);
    } catch (err) {
      console.error('distribution: toggle to-publish mark failed', err);
      addToast(t('distribution.publish.markUpdateFailed', 'Could not update publish mark'), 'error');
      setMarkedIds((prev) => {
        const next = new Set(prev);
        if (wasMarked) next.add(videoId); else next.delete(videoId);
        return next;
      });
    }
  };

  // Insert a synthetic Library row for a promoted generation so the selected
  // thumbs can resolve it even when the resource lives outside the current
  // workspace listing (promote targets the personal scope).
  const ensureVideoRow = (resourceId: string, name: string, thumbnailUrl: string | null = null) =>
    setVideos((prev) => (prev.some((v) => v.id === resourceId)
      ? prev
      : [{ id: resourceId, filename: name, thumbnail_url: thumbnailUrl }, ...prev]));

  // Pick a generated video: promote it into the Library on first pick
  // (idempotent server-side via the promoted_resource_id backlink), then
  // toggle the resulting resource id like any other selection.
  const onPickGenerated = async (g: GeneratedVideo) => {
    const fallbackName = g.name || t('distribution.publish.generatedUntitled', 'Generated video');
    const known = genResourceIds[g.id];
    if (known) {
      ensureVideoRow(known, fallbackName);
      setSelectedVideos((s) => toggle(s, known));
      return;
    }
    if (promotingId) return;
    setPromotingId(g.id);
    try {
      const resourceId = await promoteGeneratedVideo(g.id);
      setGenResourceIds((prev) => ({ ...prev, [g.id]: resourceId }));
      ensureVideoRow(resourceId, fallbackName);
      setSelectedVideos((s) => (s.includes(resourceId) ? s : [...s, resourceId]));
    } catch (err) {
      console.error('distribution: promote generated video failed', err);
      addToast(t('distribution.publish.promoteFailed', 'Could not add generated video'), 'error');
    } finally {
      setPromotingId(null);
    }
  };

  // ── Gallery cards (Images mode) ──
  // A gallery is one Library row that stands in for an ordered group of child
  // images. The gallery id itself is NEVER published — picking a gallery
  // expands it into its child image ids (added to `selectedVideos` in position
  // order) so the publish payload carries only image resource ids and the
  // downstream workflow needs zero changes. Toggle semantics: every child
  // already selected → remove the whole group; otherwise add the missing ones.
  const isGalleryRow = useCallback(
    (v: LibraryVideo): boolean => v.mime_type === GALLERY_MIME,
    [],
  );

  const onPickGallery = async (v: LibraryVideo) => {
    if (expandingGalleryId) return;
    let childIds = galleryChildren[v.id];
    if (!childIds) {
      setExpandingGalleryId(v.id);
      try {
        const children = await getGalleryItems(v.id);
        // Defensive sort — never trust the API to return position order.
        const ordered = [...children].sort((a, b) => a.position - b.position);
        childIds = ordered.map((c) => String(c.id));
        setGalleryChildren((prev) => ({ ...prev, [v.id]: childIds as string[] }));
        // Gallery children are hidden from the picker list (backend NOT EXISTS),
        // so seed synthetic rows carrying each child's own cover for the
        // selected-thumbs strip.
        ordered.forEach((c) => {
          const thumb = c.thumbnail_path ? getResourceCoverUrl(String(c.id)) : null;
          ensureVideoRow(String(c.id), c.filename || 'Image', thumb);
        });
      } catch (err) {
        console.error('distribution: expand gallery failed', err);
        addToast(t('distribution.publish.galleryExpandFailed', 'Could not open gallery'), 'error');
        return;
      } finally {
        setExpandingGalleryId(null);
      }
    }
    if (!childIds || childIds.length === 0) {
      addToast(t('distribution.publish.galleryEmpty', 'This gallery has no images'), 'info');
      return;
    }
    const ids = childIds;
    setSelectedVideos((s) => {
      const allPresent = ids.every((id) => s.includes(id));
      if (allPresent) return s.filter((id) => !ids.includes(id));
      const set = new Set(s);
      return [...s, ...ids.filter((id) => !set.has(id))];
    });
  };

  // ── Inline image upload (Images mode only) ──
  // Open the hidden file input. Guarded while a batch is in flight.
  const onUploadClick = () => {
    if (uploading) return;
    fileInputRef.current?.click();
  };

  /**
   * Fold one file's progress reading into the batch total.
   *
   * The per-file figure is capped at that file's own size because the browser
   * counts the multipart framing too, so `loaded` runs a few hundred bytes
   * past `file.size` at the end — uncapped, a finished batch reports more
   * bytes sent than the batch contains.
   */
  const noteUploadProgress = (index: number, loaded: number, cap: number, at: number) => {
    uploadLoadedRef.current[index] = Math.min(loaded, cap);
    const loadedBytes = uploadLoadedRef.current.reduce((sum, n) => sum + n, 0);
    setUploadBatch((prev) => (prev === null ? prev : {
      ...prev,
      loadedBytes,
      rate: foldUploadRate(prev.rate, { loaded: loadedBytes, at }),
    }));
  };

  // Upload one file into the current scope's My Uploads root (source_type
  // 'upload' is the endpoint default). Returns the new resource row, or a
  // typed note about why it did not, so one failure never aborts the rest of
  // the batch AND never disappears silently either.
  const uploadOne = async (
    file: File,
    index: number,
    signal: AbortSignal,
  ): Promise<{ id: string; name: string } | UploadFailureNote> => {
    try {
      const r = await uploadResource(
        file,
        scopeId,
        undefined,
        (sample) => noteUploadProgress(index, sample.loaded, file.size, sample.at),
        undefined,
        signal,
      );
      return { id: r.id, name: r.filename };
    } catch (err) {
      console.error('distribution: inline image upload failed', err);
      return describeUploadFailure(file.name, err);
    }
  };

  /** Cancel the batch in flight. Aborts the requests themselves — a button
   *  that only hid the panel would leave the bytes going. */
  const onCancelUpload = () => {
    uploadAbortRef.current?.abort();
  };

  // Upload the picked images (concurrency ≤ UPLOAD_CONCURRENCY), then insert the
  // successes into the Library rows and auto-select them IN PICK ORDER — the
  // result slot is keyed by the original index, so completion timing can't
  // reorder the gallery. Failures are kept with their reasons.
  const onFilesSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    // Reset so re-picking the same file still fires a change event.
    e.target.value = '';
    if (files.length === 0) return;
    const controller = new AbortController();
    uploadAbortRef.current = controller;
    uploadLoadedRef.current = new Array(files.length).fill(0);
    setUploadFailures([]);
    setUploadBatch({
      fileCount: files.length,
      totalBytes: files.reduce((sum, f) => sum + f.size, 0),
      loadedBytes: 0,
      rate: emptyUploadRate(),
    });
    setUploading(true);
    try {
      const results: Array<{ id: string; name: string } | UploadFailureNote | null> =
        new Array(files.length).fill(null);
      let cursor = 0;
      const worker = async () => {
        while (cursor < files.length) {
          const idx = cursor;
          cursor += 1;
          results[idx] = await uploadOne(files[idx], idx, controller.signal);
        }
      };
      await Promise.all(
        Array.from({ length: Math.min(UPLOAD_CONCURRENCY, files.length) }, worker),
      );
      const failures: UploadFailureNote[] = [];
      let cancelled = 0;
      results.forEach((res) => {
        if (res === null) { cancelled += 1; return; }
        if ('reason' in res) {
          // An abort is the user's own doing, not a failure to report back.
          if (res.reason === 'aborted') cancelled += 1;
          else failures.push(res);
          return;
        }
        ensureVideoRow(res.id, res.name);
        setSelectedVideos((s) => (s.includes(res.id) ? s : [...s, res.id]));
      });
      setUploadFailures(failures);
      if (cancelled > 0) {
        addToast(
          t(
            'distribution.publish.uploadCancelled',
            '{{n}} file(s) were not uploaded — you cancelled.',
            { n: cancelled },
          ),
          'info',
        );
      }
      if (failures.length > 0) {
        addToast(
          t('distribution.publish.uploadFailed', '{{n}} image(s) failed to upload', {
            n: failures.length,
          }),
          'error',
        );
      }
    } finally {
      setUploading(false);
      setUploadBatch(null);
      uploadAbortRef.current = null;
    }
  };

  // ── AI toggle ⇄ self declaration ──
  // Product decision (mirrored on the backend in `resolve_self_declaration`):
  // auto-map, allow override. `ai_content` existed for two releases and never
  // reached the platform — flipping it now fills in the matching declaration,
  // and the user can still change it afterwards.
  const onToggleAiContent = () => {
    setAiContent((was) => {
      const next = !was;
      if (next && selfDeclaration === '') {
        setSelfDeclaration(DECLARATION_AI);
        setDeclarationAuto(true);
      } else if (!next && declarationAuto) {
        setSelfDeclaration('');
        setDeclarationAuto(false);
      }
      return next;
    });
  };

  const onSelfDeclarationChange = (value: string) => {
    setSelfDeclaration(value as SelfDeclaration | '');
    setDeclarationAuto(false);
  };

  // Flagged as AI but declaring something else (including "nothing to
  // declare"). Not blocked — the declaration is the user's call — but they
  // should see that the two controls now disagree.
  const declarationConflict =
    aiContent && selfDeclaration !== '' && selfDeclaration !== DECLARATION_AI;

  /**
   * The schedule window, in the platforms' own numbers.
   *
   * Same discipline as `imageLimits`: read from the capabilities response, not
   * restated here, and when several platforms are in play the STRICTEST stated
   * bound wins (max of the minima, min of the maxima). A platform that states
   * nothing contributes nothing; if none of them state anything the module
   * fallback applies, so the picker is never unbounded.
   */
  const scheduleLimits = useMemo(() => {
    const caps = targetAccounts
      .map((a) => capabilities?.[a.platform])
      .filter((c): c is PlatformCapability => Boolean(c));
    const leads = caps
      .map((c) => c.schedule_min_lead_seconds)
      .filter((n): n is number => typeof n === 'number');
    const aheads = caps
      .map((c) => c.schedule_max_ahead_seconds)
      .filter((n): n is number => typeof n === 'number');
    return {
      minLeadMs: (leads.length ? Math.max(...leads) : DEFAULT_MIN_LEAD_S) * 1000,
      maxAheadMs: (aheads.length ? Math.min(...aheads) : DEFAULT_MAX_AHEAD_S) * 1000,
    };
  }, [targetAccounts, capabilities]);

  const scheduleIssue = scheduleMode === 'schedule'
    ? scheduleProblem(scheduledAt, Date.now(), scheduleLimits.minLeadMs, scheduleLimits.maxAheadMs)
    : null;

  /**
   * Window edges as instants, handed to the picker so out-of-window days /
   * hours / minutes render disabled — the user cannot select an illegal time in
   * the first place. Recomputed every time the popover opens rather than once
   * on mount: a form left sitting for an hour would otherwise offer a floor
   * that has already passed.
   */
  const scheduleWindow = useMemo(() => {
    const now = Date.now();
    return {
      minAt: new Date(now + scheduleLimits.minLeadMs),
      maxAt: new Date(now + scheduleLimits.maxAheadMs),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scheduleLimits, scheduleAnchor]);

  /** A duration → the words a person reads ("2 hours 10 minutes", "14 days"). */
  const leadWords = (ms: number): string => {
    const totalMinutes = Math.round(ms / 60000);
    const dayMinutes = 24 * 60;
    if (totalMinutes >= dayMinutes && totalMinutes % dayMinutes === 0) {
      return t('common.duration.days', '{{value}} days', { value: totalMinutes / dayMinutes });
    }
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    if (hours === 0) return t('common.duration.minutes', '{{value}} minutes', { value: minutes });
    if (minutes === 0) return t('common.duration.hours', '{{value}} hours', { value: hours });
    return t('common.duration.hoursMinutes', '{{hours}} hours {{minutes}} minutes', {
      hours,
      minutes,
    });
  };
  const minLeadWords = leadWords(scheduleLimits.minLeadMs);
  const maxAheadWords = leadWords(scheduleLimits.maxAheadMs);

  // Scheduling / declaration / collection are creator-page controls: only an
  // account bound by QR code publishes through that page. On any other account
  // the backend fails the row rather than dropping the field, so warn before
  // the user finds out from a failed record.
  const usesCreatorPageOnlyFields =
    scheduleMode === 'schedule'
    || selfDeclaration !== ''
    || collectionName.trim().length > 0
    // Music is set by clicking through the creator page, so it is in the same
    // group: an OAuth/H5 account cannot honour it, and the backend fails that
    // row rather than dropping the field.
    || musicName.trim().length > 0;
  const nonSessionSelected = useMemo(
    () => selectedAccounts.filter(
      (id) => accounts.find((a) => a.id === id)?.auth_type !== 'session',
    ).length,
    [selectedAccounts, accounts],
  );

  /**
   * One backend rejection reason → the sentence a user can act on.
   *
   * The backend already sends English prose in `message`, and printing that
   * verbatim would be the easy option. It is the wrong one: those strings are
   * written for logs ("content_type 'images' not supported on douyin session
   * channel"), they name internals, and they can never be translated. The
   * `reason` code is the contract — this switch is the only place that knows
   * what each code means to a person.
   *
   * An unmapped code still shows the code rather than a shrug: a reason we
   * have not written copy for is a gap to fix, and hiding it behind "could not
   * publish" is how it stays hidden.
   */
  const gateProblemText = useCallback((p: PublishGateProblem): string => {
    const account = accounts.find((a) => a.id === String(p.account_id ?? ''));
    const body = ((): string => {
      switch (p.reason) {
        case 'too_many_images':
          return imageLimits.max !== null
            ? t(
              'distribution.publish.gateTooManyImages',
              'Too many images — at most {{max}} fit in one post. Remove some and try again.',
              { max: imageLimits.max },
            )
            : t(
              'distribution.publish.gateTooManyImagesNoLimit',
              'Too many images for at least one of the accounts you picked.',
            );
        case 'too_few_images':
          return imageLimits.min !== null
            ? t(
              'distribution.publish.gateTooFewImages',
              'An image post needs at least {{min}} image(s).',
              { min: imageLimits.min },
            )
            : t(
              'distribution.publish.gateTooFewImagesNoLimit',
              'Not enough images for at least one of the accounts you picked.',
            );
        case 'account_not_session_bound':
          return t(
            'distribution.publish.gateAccountNotSessionBound',
            'Image posts need an account connected by QR code — this one would fall back to the phone handoff. Remove it, or reconnect it by QR code.',
          );
        case 'cover_not_supported_for_images':
          return t(
            'distribution.publish.gateCoverNotSupportedForImages',
            'Image posts take their cover from the images themselves — a separate cover cannot be sent.',
          );
        case 'unsupported_content_type':
          return t(
            'distribution.publish.gateUnsupportedContentType',
            'This account cannot publish this kind of post.',
          );
        case 'publishing_not_implemented':
          return t(
            'distribution.publish.gatePublishingNotImplemented',
            'Publishing is not available for this platform yet — the account can be connected but not posted to.',
          );
        case 'title_empty':
          return t('distribution.publish.gateTitleEmpty', 'Add a title before publishing.');
        case 'title_too_long':
          return t(
            'distribution.publish.gateTitleTooLong',
            'The title is too long for this platform — shorten it and try again.',
          );
        case 'too_many_topics':
          return t(
            'distribution.publish.gateTooManyTopics',
            'Too many topics for this platform — remove some and try again.',
          );
        case 'invalid_schedule':
        case 'scheduling_not_supported':
          return t(
            'distribution.publish.gateScheduleRejected',
            'The scheduled time was refused — pick another time, or publish now.',
          );
        case 'collections_not_supported':
        case 'invalid_collection_name':
          return t(
            'distribution.publish.gateCollectionRejected',
            'The collection name was refused — clear it, or use one this account already has.',
          );
        case 'music_not_supported':
          return t(
            'distribution.publish.gateMusicNotSupported',
            'This account cannot have its music picked for it — clear the music field, or remove the account.',
          );
        case 'invalid_music_name':
          return t(
            'distribution.publish.gateMusicInvalid',
            'That music name was refused — shorten it, or clear the field to publish with the platform default.',
          );
        // A picked track arrived without its id. Separate copy from the one
        // above because the user's move differs: a long name is his to fix,
        // an unidentifiable track is ours — the only useful advice is "pick
        // it again", and telling him to shorten something would be nonsense.
        case 'invalid_music_reference':
          return t(
            'distribution.publish.gateMusicRefInvalid',
            'That track could not be identified — pick it again from the music panel.',
          );
        case 'self_declaration_not_supported':
        case 'unknown_self_declaration':
          return t(
            'distribution.publish.gateDeclarationRejected',
            'The self declaration was refused — pick another one, or leave it unset.',
          );
        case 'unknown_visibility':
          return t(
            'distribution.publish.gateUnknownVisibility',
            'That visibility is not available on this platform.',
          );
        default:
          return t(
            'distribution.publish.gateUnknownReason',
            'The publish request was refused ({{reason}}).',
            { reason: p.reason },
          );
      }
    })();
    return account
      ? t('distribution.publish.gateProblemForAccount', '{{account}} — {{problem}}', {
        account: account.username,
        problem: body,
      })
      : body;
  }, [accounts, imageLimits, t]);

  const canPublish = useMemo(
    () => selectedVideos.length > 0
      && selectedAccounts.length > 0
      && title.trim().length > 0
      && scheduleIssue === null
      // Images mode can only be entered while it is supported, but the
      // supported set is derived from the SELECTED accounts — picking one more
      // account can close the gate afterwards. Refusing here beats letting the
      // platform refuse after the upload.
      && (!isImages || imagesSupported)
      // Same argument for the count: the bound belongs to the accounts, so
      // adding an account can put an already-picked gallery out of range.
      && imageCountProblem === null,
    [
      selectedVideos, selectedAccounts, title, scheduleIssue, isImages, imagesSupported,
      imageCountProblem,
    ],
  );

  const postsBroadcast = selectedVideos.length * selectedAccounts.length;
  const postsOneToOne = selectedVideos.length > 0 ? selectedAccounts.length : 0;
  // Images = ONE note per account carrying every picked image, so the post
  // count is just the account count (the gallery is never split).
  const totalPosts = isImages
    ? (selectedVideos.length > 0 ? selectedAccounts.length : 0)
    : (mode === 'broadcast' ? postsBroadcast : postsOneToOne);

  const firstSelectedAccount = useMemo(
    () => accounts.find((a) => a.id === selectedAccounts[0]),
    [accounts, selectedAccounts],
  );
  /**
   * The handle the preview attributes the post to, or null when no account is
   * picked yet.
   *
   * ⚠️ This used to fall back to the literal string `yourhandle`, rendered in
   * the same position and the same style as a real one. A handle nobody owns,
   * presented as this post's author, is the same defect as a fabricated like
   * count — the preview panel now renders nothing rather than something
   * invented. See the header comment in PublishPreview.tsx.
   */
  const previewHandle = firstSelectedAccount?.username ?? null;

  /**
   * What the preview may say about the post's sound — the three cases are
   * documented on `PreviewSoundtrack`, and this is the only place that has the
   * information to tell them apart.
   *
   * ⚠️ The three-way split is the whole point. A picked track is a known
   * title; an untouched music control means the platform keeps the clip's own
   * audio (see the `musicName` state's own note); a typed keyword with no
   * track picked is the one case where nobody knows what will be attached —
   * one search comes back with several character-identical titles under
   * different ids, which is exactly why `music_ambiguous` exists. Folding the
   * third case into either of the other two would put a claim on screen that
   * the publish step itself refuses to make.
   */
  const previewSoundtrack: PreviewSoundtrack = musicTrack !== null
    ? {
      kind: 'track',
      label: musicTrack.author.trim() === ''
        ? musicTrack.title
        : `${musicTrack.title} - ${musicTrack.author}`,
    }
    : musicName.trim() === ''
      ? { kind: 'platformDefault' }
      : { kind: 'unresolved' };

  /**
   * Whole-percent progress for the batch, or null when there is nothing to
   * divide by (a pick of zero-byte files). Null renders as no figure at all —
   * `0%` would be a reading of a thing that was never measured.
   */
  const uploadPercent = uploadBatch === null || uploadBatch.totalBytes <= 0
    ? null
    : Math.min(100, Math.round((uploadBatch.loadedBytes / uploadBatch.totalBytes) * 100));

  /** One sentence per typed failure reason. Every branch is reachable from
   *  `classifyUploadStatus` or from a throw we could not classify. */
  const uploadReasonText = (reason: UploadFailureReason | 'unknown'): string => {
    switch (reason) {
      case 'unauthorized':
        return t(
          'distribution.publish.uploadFailUnauthorized',
          'This workspace did not accept the upload — check you still have access to it.',
        );
      case 'too_large':
        return t('distribution.publish.uploadFailTooLarge', 'The file is over the size limit.');
      case 'network':
        return t(
          'distribution.publish.uploadFailNetwork',
          'The connection dropped before the file finished sending.',
        );
      case 'server':
        return t('distribution.publish.uploadFailServer', 'The server could not store the file.');
      case 'rejected':
        return t('distribution.publish.uploadFailRejected', 'The server refused this file.');
      case 'malformed':
        return t(
          'distribution.publish.uploadFailMalformed',
          'The upload finished but the reply could not be read, so it is not certain the file was stored.',
        );
      case 'aborted':
      case 'unknown':
      default:
        return t(
          'distribution.publish.uploadFailUnknown',
          'The upload failed, and the reason was not one this page recognises.',
        );
    }
  };

  const visLabel = (v: Visibility): string => {
    if (v === 'public') return t('distribution.publish.vis_public', 'Public');
    if (v === 'private') return t('distribution.publish.vis_private', 'Private');
    return t('distribution.publish.visFriends', 'Friends');
  };

  // `blocked` covers both dead statuses (see `needsReconnect`) — the row is the
  // second lock, this is the first: whichever way the click arrives, an account
  // that cannot publish must not end up in the selection.
  const onToggleAccount = (accountId: string, blocked: boolean) => {
    if (blocked) return;
    setSelectedAccounts((s) => toggle(s, accountId));
  };

  const onAccountTitleChange = (accountId: string, value: string) => {
    setAccountConfigs((prev) => ({ ...prev, [accountId]: { title: value } }));
  };

  const onPublish = async () => {
    if (!canPublish || submitting) return;
    setSubmitting(true);
    setGateProblems([]);
    try {
      const accountConfigsPayload = Object.fromEntries(
        Object.entries(accountConfigs)
          .filter(([id, cfg]) => selectedAccounts.includes(id) && cfg.title.trim().length > 0)
          .map(([id, cfg]) => [id, { title: cfg.title.trim() }]),
      );
      await createPublishTask({
        // Which workspace this batch belongs to. The page already knew — it
        // lists Library media with the same `scopeId` — but never told the
        // backend, so every publish_task landed with `team_id = NULL`, the
        // issue mirrored from it inherited no team, and the To-do list (whose
        // every scope filters on `team_id`) hid the user's own failed batches.
        //
        // Sent in BOTH workspaces: a personal workspace is a real team row, so
        // there is no "no team" case to special-case here. Omitted only while
        // the team context is still loading — the backend then falls back to
        // the caller's personal team rather than writing NULL.
        team_id: scopeId || undefined,
        content_type: contentType,
        resource_ids: selectedVideos,
        title: title.trim(),
        description: description.trim() || undefined,
        topics: topics.length ? topics : undefined,
        // Derived from the CURRENT topic list rather than tracked alongside it:
        // removing a chip therefore drops its binding for free, and a binding
        // for a topic that is no longer in the post can never leak out.
        topic_refs: pickedTopicRefs.length ? pickedTopicRefs : undefined,
        visibility,
        ai_content: aiContent,
        allow_download: allowDownload,
        // Images always broadcast (one note per account); force it so a stale
        // one_to_one selection can't leak into the payload.
        distribution_mode: isImages ? 'broadcast' : mode,
        channel,
        // Sent as an absolute instant. The input holds local wall clock; the
        // backend refuses a value without an offset rather than guessing.
        scheduled_at: scheduleMode === 'schedule' && scheduledAt
          ? new Date(scheduledAt).toISOString()
          : undefined,
        // Omitted (not null) when unset — "leave the control alone" and
        // "declare nothing" are different instructions on the platform.
        self_declaration: selfDeclaration || undefined,
        collection_name: collectionName.trim() || undefined,
        // Omitted when blank — "leave the music control alone" (publish on the
        // platform default) is a real instruction, not a missing value.
        music_name: musicName.trim() || undefined,
        // The identity of the picked track. Present = the browser aligns rows
        // on (title, author, length) and refuses anything short of a unique
        // match; absent = the older title-only path, which is fuzzy on
        // purpose. `music_name` above is the search keyword either way, and
        // the backend re-derives it from this object so the two cannot drift.
        music_ref: musicTrack
          ? {
            music_id: musicTrack.music_id,
            music_name: musicTrack.title,
            music_author: musicTrack.author,
            duration: musicTrack.duration,
            user_count: musicTrack.user_count,
            cover_url: musicTrack.cover_url,
          }
          : undefined,
        // Cover-first order: the pair was already derived by
        // POST /covers/select, so it rides along at create time rather than
        // needing a second call against the new task.
        //
        // Never for images (D4): the platform's image-post cover is CHOSEN
        // FROM the uploaded images, not uploaded separately, so a cover asset
        // here is a semantic error and the backend rejects the batch with
        // `cover_not_supported_for_images`. Switching content type already
        // clears `covers`; forcing it here means a future path that forgets to
        // cannot arm that rejection.
        cover_vertical_resource_id: isImages ? undefined : covers?.vertical,
        cover_horizontal_resource_id: isImages ? undefined : covers?.horizontal,
        account_ids: selectedAccounts,
        account_configs: Object.keys(accountConfigsPayload).length ? accountConfigsPayload : undefined,
      });
      addToast(t('distribution.publish.queued', 'Publish task created'), 'success');
      navigate('../records');
    } catch (err) {
      console.error('distribution: create publish task failed', err);
      // A typed refusal (422) is not "something went wrong" — the backend
      // knows exactly what is wrong and which account it is about. Show that,
      // in words, and keep it on screen: a toast that fades takes the only
      // explanation with it while the form is still unfixed.
      const problems = publishGateProblems(err);
      if (problems) {
        setGateProblems(problems);
        addToast(
          t('distribution.publish.rejected', 'This post was refused — see the summary'),
          'error',
        );
      } else {
        addToast(t('distribution.publish.failed', 'Could not create publish task'), 'error');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="dist-v4">
      <PageHeader
        level="content"
        title={t('distribution.publish.title', 'Publish')}
        subtitle={t('distribution.publish.subtitle', 'Send library media to your connected accounts.')}
      />

      <div className="stepper" aria-hidden="true">
        <span className="step done">
          <span className="n"><Check size={11} /></span>
          {t('distribution.publish.content', 'Content')}
        </span>
        <span className="step-line done" />
        <span className="step cur"><span className="n">2</span>{t('distribution.publish.stepDetailsAccounts', 'Details & accounts')}</span>
        <span className="step-line" />
        <span className="step"><span className="n">3</span>{t('distribution.publish.stepDone', 'Done')}</span>
      </div>

      <div className="pub-cols">
        {/* ── left: form ── */}
        <div className="pub-form">
          <div className="fcard">
            <h4>
              {t('distribution.publish.content', 'Content')}
              <span className="aux">
                {/* The maximum is quoted from the capabilities response, so it
                    is right for whatever the accounts actually allow — and it
                    is visible BEFORE the user picks a 36th image. */}
                {isImages
                  ? (imageLimits.max !== null
                    ? t(
                      'distribution.publish.imagesSelectedOfMax',
                      'Images · {{n}} of up to {{max}} selected',
                      { n: selectedVideos.length, max: imageLimits.max },
                    )
                    : t('distribution.publish.imagesSelectedCount', 'Images · {{n}} selected', { n: selectedVideos.length }))
                  : t('distribution.publish.videoSelectedCount', 'Video · {{n}} selected', { n: selectedVideos.length })}
              </span>
            </h4>
            <div className="seg" style={{ marginBottom: 10 }} role="tablist" aria-label={t('distribution.publish.contentType', 'Content type')}>
              <button
                type="button"
                role="tab"
                aria-selected={!isImages}
                className={!isImages ? 'on' : ''}
                onClick={() => onContentTypeChange('video')}
              >
                {t('distribution.publish.contentTypeVideo', 'Video')}
              </button>
              {/* Disabled, not removed: image posts are a planned feature and
                  every images code path below is kept alive for them. What is
                  removed is the CLAIM — until the browser service can post a
                  gallery, offering the choice only moves the refusal to the
                  last step of a form the user already filled in. */}
              <button
                type="button"
                role="tab"
                aria-selected={isImages}
                disabled={!imagesSupported}
                // While the lookup is in flight the tab is busy, not judged.
                // Same `disabled` either way — arming a post we have no evidence
                // for is what we are avoiding — but a screen reader should hear
                // "we're checking", not silence.
                aria-busy={imagesGate === 'loading'}
                title={imagesGateHint}
                className={isImages ? 'on' : ''}
                onClick={() => onContentTypeChange('images')}
              >
                {t('distribution.publish.contentTypeImages', 'Images')}
              </button>
            </div>
            {!imagesSupported && (
              <p className="hint" style={{ marginBottom: 10 }}>
                {imagesGate === 'loading' && (
                  <Loader2 size={12} className="animate-spin" style={{ marginRight: 6, verticalAlign: '-2px' }} />
                )}
                {imagesGateHint}
                {/* A dead end the user can act on. The copy says our lookup
                    failed; without a way to ask again that is just a nicer
                    dead end. */}
                {imagesGate === 'unknown' && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    style={{ marginLeft: 8 }}
                    onClick={() => void loadCapabilities()}
                  >
                    <RefreshCw size={12} /> {t('distribution.publish.capabilitiesRetry', 'Check again')}
                  </button>
                )}
              </p>
            )}
            <div className="seg">
              <button type="button" className="on">{t('distribution.publish.fromLibrary', 'From Library')}</button>
              {isImages ? (
                <button
                  type="button"
                  disabled={uploading}
                  aria-busy={uploading}
                  onClick={onUploadClick}
                >
                  {uploading ? (
                    <>
                      <Loader2 size={12} className="animate-spin" />
                      {t('distribution.publish.uploading', 'Uploading…')}
                    </>
                  ) : (
                    t('distribution.publish.upload', 'Upload')
                  )}
                </button>
              ) : (
                // Video uploads stay locked — large files need a resumable path.
                <button type="button" disabled title={t('distribution.comingInD3', 'Coming in D3')}>{t('distribution.publish.upload', 'Upload')}</button>
              )}
            </div>
            {isImages && (
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                hidden
                aria-label={t('distribution.publish.uploadImagesAria', 'Upload images')}
                onChange={onFilesSelected}
              />
            )}
            {/* ══ THE UPLOAD IN FLIGHT ══════════════════════════════════════
                A spinner says "something is happening". These say how much of
                it is done, how fast, and give the reader a way out. Every
                figure here comes off the browser's own progress events; the
                only derived one is the rate, and it says it is approximate.
                There is deliberately NO remaining-time figure — see
                `foldUploadRate` for why. */}
            {uploadBatch !== null && (
              <div className="up-panel" role="status" aria-live="polite">
                <div className="up-head">
                  <span className="up-title">
                    {t('distribution.publish.uploadingCount', 'Uploading {{n}} file(s)', {
                      n: uploadBatch.fileCount,
                    })}
                  </span>
                  {uploadPercent !== null && (
                    <span className="up-pct">{`${uploadPercent}%`}</span>
                  )}
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm up-cancel"
                    onClick={onCancelUpload}
                  >
                    {t('distribution.publish.uploadCancel', 'Cancel Upload')}
                  </button>
                </div>
                <div
                  className="up-bar"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  /* Omitted rather than sent as 0 when the batch has no bytes
                     to divide by: `aria-valuenow="0"` is a reading, and an
                     indeterminate bar is not at zero, it is unmeasured. */
                  aria-valuenow={uploadPercent ?? undefined}
                >
                  <span style={{ width: `${uploadPercent ?? 0}%` }} />
                </div>
                <div className="up-rows">
                  <span>
                    {t('distribution.publish.uploadBytes', '{{done}} of {{total}}', {
                      done: formatBytes(uploadBatch.loadedBytes),
                      total: formatBytes(uploadBatch.totalBytes),
                    })}
                  </span>
                  <span>
                    {uploadBatch.rate.bytesPerSecond === null
                      ? t('distribution.publish.uploadSpeedPending', 'Measuring speed…')
                      : t('distribution.publish.uploadSpeed', '≈ {{rate}}/s', {
                        rate: formatBytes(Math.round(uploadBatch.rate.bytesPerSecond)),
                      })}
                  </span>
                </div>
                <p className="up-warn">
                  {t(
                    'distribution.publish.uploadKeepFiles',
                    'Leave these files where they are until the upload finishes.',
                  )}
                </p>
              </div>
            )}

            {/* Typed failure read-out. One line per file that did not make it,
                naming the file and what went wrong — and the backend's own
                words when it sent any, which is where "Maximum size is 500 MB"
                comes from. The toast alongside is a count; this is the answer
                to "which one, and why". */}
            {uploadFailures.length > 0 && (
              <ul className="up-fails">
                {uploadFailures.map((f, i) => (
                  <li key={`${f.name}-${String(i)}`}>
                    <AlertTriangle size={12} aria-hidden="true" />
                    <b>{f.name}</b>
                    <span>{uploadReasonText(f.reason)}</span>
                    {f.detail !== null && <em>{f.detail}</em>}
                  </li>
                ))}
              </ul>
            )}
            <div className="thumbs">
              {selectedVideoObjs.map((v, idx) => {
                const hasImg = Boolean(v.thumbnail_url);
                return (
                  <div
                    key={v.id}
                    aria-label={v.filename}
                    title={v.filename}
                    className={`thumb ${hasImg ? '' : idx % 2 === 0 ? 't1' : 't2'}`}
                    style={hasImg ? { backgroundImage: `url(${v.thumbnail_url})` } : undefined}
                  >
                    <button
                      type="button"
                      className="rm"
                      aria-label={t('distribution.publish.removeVideo', 'Remove {{name}}', { name: v.filename })}
                      onClick={() => removeVideo(v.id)}
                    >
                      ×
                    </button>
                    {isImages ? (
                      <>
                        <span
                          className="ord"
                          aria-label={t('distribution.publish.imageOrder', 'Image {{n}}', { n: idx + 1 })}
                        >
                          {idx + 1}
                        </span>
                        {/* Order is the gallery order the note is published
                            with, and until now the only way to change it was
                            to deselect everything and re-pick in the right
                            sequence. Buttons rather than drag-and-drop: the
                            numbered badge already states the order, and a drag
                            affordance would need its own keyboard and touch
                            story to be usable at all.
                            Disabled at the ends, not silently inert — an
                            enabled control that does nothing reads as a bug. */}
                        <span className="ord-move">
                          <button
                            type="button"
                            disabled={selectedVideos.indexOf(v.id) === 0}
                            aria-label={t('distribution.publish.moveImageEarlier', 'Move {{name}} earlier', { name: v.filename })}
                            onClick={() => moveImage(v.id, -1)}
                          >
                            <ChevronLeft size={12} />
                          </button>
                          <button
                            type="button"
                            disabled={selectedVideos.indexOf(v.id) === selectedVideos.length - 1}
                            aria-label={t('distribution.publish.moveImageLater', 'Move {{name}} later', { name: v.filename })}
                            onClick={() => moveImage(v.id, 1)}
                          >
                            <ChevronRight size={12} />
                          </button>
                        </span>
                      </>
                    ) : (
                      <span className="play">
                        <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                      </span>
                    )}
                  </div>
                );
              })}
              <button
                type="button"
                className="thumb add"
                onClick={() => { setPickerQuery(''); setPickerOpen(true); }}
              >
                <Plus size={16} />
                {t('distribution.publish.addFromLibrary', 'Add from Library')}
              </button>
            </div>
            {/* Both bounds are the platform's, read from the capabilities
                response — saying them here is what keeps the user from
                discovering them from a rejected submit. */}
            {imageCountProblem === 'tooMany' && (
              <p className="field-err">
                {t(
                  'distribution.publish.imagesTooMany',
                  'You picked {{n}} images — at most {{max}} fit in one post. Remove {{over}}.',
                  {
                    n: selectedVideos.length,
                    max: imageLimits.max,
                    over: selectedVideos.length - (imageLimits.max ?? 0),
                  },
                )}
              </p>
            )}
            {imageCountProblem === 'tooFew' && (
              <p className="field-err">
                {t(
                  'distribution.publish.imagesTooFew',
                  'An image post needs at least {{min}} image(s) — you picked {{n}}.',
                  { n: selectedVideos.length, min: imageLimits.min },
                )}
              </p>
            )}
          </div>

          {/* Cover — a video-post section, and only that.
              An image post has no cover to set: the platform takes it FROM the
              first uploaded image, and a gallery carrying a separate cover
              asset is refused outright (`cover_not_supported_for_images`), so
              there is no second upload to offer.
              [实测 2026-08-11] the image-post page shows 封面设置 / 选择一张图片
              作为封面 while the video page's 设置封面 is absent.

              ⚠️ ABSENT, NOT DISABLED — and not a card of explanatory prose
              either, which is what stood here before. The house rule is to
              disable and say why, because a control nobody can see is a
              control nobody can ask about; that rule is about things which
              SHOULD apply and happen not to yet. This one can never apply, and
              a whole card whose entire content was "this section does not
              apply to you" is a section the reader has to read in order to
              learn it was not for them. The fact it carried is not lost: the
              Summary on the right still states that the first image is the
              cover, in one line, where the rest of the post's facts are. */}
          {!isImages && (
            <div className="fcard">
              <h4>
                {t('distribution.publish.cover', 'Cover')}
                <span className="aux">
                  {covers
                    ? t('distribution.publish.coverSet', 'Vertical + horizontal ready')
                    : t('distribution.publish.notSetYet', 'Not set yet')}
                </span>
              </h4>
              <div className="cover-wrap">
                {/* Covers come from a frame of the video being published, so the
                    picker needs the same selection the content card holds. */}
                <CoverPicker
                  sources={selectedVideoObjs}
                  value={covers}
                  onChange={setCovers}
                  onOpenStudio={() => setCoverStudioOpen(true)}
                />
                <div className="cover-ai">
                  <div className="head">
                    <b><Sparkles size={14} />{t('distribution.publish.aiCoversCanvas', 'AI covers · Canvas')}</b>
                    {/* A real button at last — the dead `aria-disabled` anchor
                        ("Coming in D4") lived here until 2026-08-23. Opens a
                        full-screen LAYER, not a route: this page holds ~75
                        pieces of un-persisted form state, and navigating away
                        would wipe everything the user just filled in. */}
                    <button
                      type="button"
                      className="open-studio"
                      onClick={() => setCoverStudioOpen(true)}
                      data-testid="open-cover-studio"
                    >
                      {t('distribution.publish.openCoverStudio', 'Open Cover Studio')}
                    </button>
                  </div>
                  <div className="foot">{t('distribution.publish.coverGenDesc', 'Generates candidates from a video frame + your title.')}</div>
                </div>
              </div>
              <CoverStudioOverlay
                open={coverStudioOpen}
                scopeId={scopeId ?? ''}
                sources={selectedVideoObjs}
                topic={title}
                onClose={() => setCoverStudioOpen(false)}
                onApply={(patch) => {
                  // The studio fills ONE slot per apply (the vertical tab an
                  // AI cover or a 3:4 crop, the horizontal tab a 4:3 crop);
                  // the other slot keeps whatever it already had.
                  setCovers((prev) => ({ ...(prev ?? {}), ...patch }));
                }}
              />
            </div>
          )}

          <div className="fcard">
            <h4>{t('distribution.publish.titleLabel', 'Title')} <span className="aux">{title.length} / 500</span></h4>
            <input
              className="input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={500}
              placeholder={t('distribution.publish.titlePlaceholder', 'Add a title')}
            />
            <h4 style={{ marginTop: 15 }}>{t('distribution.publish.descriptionLabel', 'Description')} <span className="aux">{description.length} / 1000</span></h4>
            <textarea
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={1000}
              rows={3}
              style={{ minHeight: 64 }}
              placeholder={t('distribution.publish.descPlaceholder', 'Add a description')}
            />
            <div className="topics">
              <button
                type="button"
                className={`chip ${topicInputOpen ? 'chip-indigo' : 'chip-mute'}`}
                aria-pressed={topicInputOpen}
                aria-expanded={topicInputOpen}
                onClick={() => setTopicInputOpen((v) => !v)}
              >
                {t('distribution.publish.topicChip', '# Topic')}
              </button>
              <span
                className="chip chip-mute chip-soon"
                aria-disabled="true"
                title={t('distribution.publish.mentionSoon', 'Mentions need the recipient’s open_id — coming later')}
              >
                {t('distribution.publish.mentionChip', '@ Mention')}
                <em className="soon">{t('distribution.publish.soon', 'Soon')}</em>
              </span>
              {topics.map((tag) => (
                <span
                  key={tag}
                  className="chip chip-topic"
                  /* Present only when this topic is bound to a platform topic
                     entity. It is also how a test (and a human with devtools)
                     can see that the binding survived the pick. */
                  data-topic-id={topicRefs[tag.toLowerCase()]?.topic_id || undefined}
                >
                  #{tag}
                  <button
                    type="button"
                    className="chip-x"
                    aria-label={t('distribution.publish.removeTopic', 'Remove topic {{tag}}', { tag })}
                    onClick={() => removeTopic(tag)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            {topicInputOpen && (
              <div className="topic-input-row" style={{ marginTop: 7 }}>
                <input
                  className="input"
                  value={topicInput}
                  maxLength={MAX_TOPIC_LEN}
                  aria-label={t('distribution.publish.topicInputAria', 'Add a topic')}
                  aria-expanded={suggestState === 'ready' && suggestions.length > 0}
                  aria-controls="topic-suggest-list"
                  placeholder={t('distribution.publish.topicInputPlaceholder', 'Type a topic, press Enter (comma or space also adds)')}
                  onChange={(e) => setTopicInput(e.target.value)}
                  onKeyDown={onTopicKeyDown}
                  onBlur={commitTopicInput}
                />
              </div>
            )}
            {/* Live suggestions from the platform's own topic library.
                Deliberately four separate states — an empty list is only ever
                shown for "the platform had nothing", never for a failure. */}
            {topicInputOpen && suggestState !== 'idle' && (
              <div
                className="topic-suggest"
                id="topic-suggest-list"
                role="listbox"
                data-testid="topic-suggest"
              >
                {suggestState === 'loading' && (
                  <div className="topic-suggest-note">
                    <Loader2 className="spin" />
                    {t('distribution.publish.topicSuggestLoading', 'Looking up topics…')}
                  </div>
                )}
                {suggestState === 'error' && (
                  <div className="topic-suggest-note topic-suggest-error" role="alert">
                    <AlertCircle />
                    {suggestErrorText()}
                  </div>
                )}
                {suggestState === 'ready' && suggestions.length === 0 && (
                  <div className="topic-suggest-note">
                    {t('distribution.publish.topicSuggestEmpty', 'No topics found for that word.')}
                  </div>
                )}
                {suggestState === 'ready' && suggestions.map((s) => (
                  <button
                    key={`${s.name}-${s.topic_id}`}
                    type="button"
                    role="option"
                    aria-selected={false}
                    className="topic-suggest-row"
                    data-topic-id={s.topic_id || undefined}
                    /* The input commits its text on blur, and blur fires before
                       click — without this the typed prefix would be added as a
                       topic and the row's own handler would never run. */
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => pickSuggestion(s)}
                  >
                    <span className="ts-name">#{s.name}</span>
                    {s.is_new ? (
                      <span className="ts-new">{t('distribution.publish.topicNew', 'New')}</span>
                    ) : (
                      <span className="ts-views">
                        {/* The number is rendered directly rather than
                            interpolated into a sentence: it is the one part of
                            this row that must survive regardless of how the
                            translation layer is wired. Only the unit word is
                            translated. */}
                        <b>{formatViewCount(s.view_count, i18n.language)}</b>
                        {' '}
                        {t('distribution.publish.topicPlays', 'plays')}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="fcard">
            <div className="frow">
              <div className="lbl"><b>{t('distribution.publish.visibility', 'Visibility')}</b></div>
              <div className="seg">
                {VIS.map((v) => (
                  <button key={v} type="button" className={visibility === v ? 'on' : ''} onClick={() => setVisibility(v)}>
                    {visLabel(v)}
                  </button>
                ))}
              </div>
            </div>
            <div className="frow">
              <div className="lbl">
                <b>{t('distribution.publish.aiContent', 'AI-generated content')}</b>
                <span>{t('distribution.publish.aiContentDesc', 'Preselects the matching self declaration below.')}</span>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={aiContent}
                aria-label={t('distribution.publish.aiContent', 'AI-generated content')}
                className={`toggle ${aiContent ? 'on' : ''}`}
                onClick={onToggleAiContent}
              />
            </div>
            <div className="frow" style={{ display: 'block' }}>
              <div className="lbl" style={{ marginBottom: 7 }}>
                <b>{t('distribution.publish.selfDeclaration', 'Self declaration')}</b>
                <span>
                  {t(
                    'distribution.publish.selfDeclarationDesc',
                    'Douyin content declaration. Leave unset to keep the platform default.',
                  )}
                </span>
              </div>
              {/* UiSelect, not a bare <select>: a native dropdown paints with
                  the OS widget (its own highlight colour, its own font) and was
                  the only control on this page that did. `triggerClassName`
                  swaps the shared component's default surface for this page's
                  own `.input` field styling, so the trigger still looks like
                  every other field in the form while the menu is the same
                  portal menu the rest of the app uses. */}
              <UiSelect
                triggerClassName="input"
                value={selfDeclaration}
                aria-label={t('distribution.publish.selfDeclaration', 'Self declaration')}
                onChange={(e) => onSelfDeclarationChange(e.target.value)}
              >
                <option value="">
                  {t('distribution.publish.declUnset', 'Not set')}
                </option>
                {SELF_DECLARATIONS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {t(`distribution.publish.${d.key}`, d.label)}
                  </option>
                ))}
              </UiSelect>
              {declarationConflict && (
                <p className="field-warn">
                  {t(
                    'distribution.publish.declConflict',
                    'This is marked as AI-generated but declares something else — the declaration you picked is what gets sent.',
                  )}
                </p>
              )}
            </div>
            <div className="frow">
              <div className="lbl">
                <b>{t('distribution.publish.allowDownloadsLabel', 'Allow downloads')}</b>
                <span>
                  {isImages
                    ? t('distribution.publish.allowDownloadsDescImages', 'Viewers can save the images to their device')
                    : t('distribution.publish.allowDownloadsDesc', 'Viewers can save the video to their device')}
                </span>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={allowDownload}
                aria-label={t('distribution.publish.allowDownloadsLabel', 'Allow downloads')}
                className={`toggle ${allowDownload ? 'on' : ''}`}
                onClick={() => setAllowDownload((v) => !v)}
              />
            </div>
            <div className="frow" style={{ display: 'block' }}>
              <div className="lbl" style={{ marginBottom: 7 }}>
                <b>{t('distribution.publish.publishTime', 'Publish time')}</b>
                <span>
                  {t(
                    'distribution.publish.scheduleWindow',
                    'Anything outside {{min}} to {{max}} from now is greyed out — the platform refuses it, and the extra lead time leaves room for the upload.',
                    { min: minLeadWords, max: maxAheadWords },
                  )}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <div className="seg">
                  <button
                    type="button"
                    className={scheduleMode === 'now' ? 'on' : ''}
                    onClick={() => { setScheduleMode('now'); setScheduleAnchor(null); }}
                  >
                    {t('distribution.publish.scheduleNow', 'Now')}
                  </button>
                  <button
                    type="button"
                    className={scheduleMode === 'schedule' ? 'on' : ''}
                    onClick={() => setScheduleMode('schedule')}
                  >
                    {t('distribution.publish.schedule', 'Schedule')}
                  </button>
                </div>
                {scheduleMode === 'schedule' ? (
                  <>
                    {/* Same control the workspace canvas schedules nodes with —
                        the native <input type="datetime-local"> that used to sit
                        here rendered as `mm/dd/yyyy, --:-- --` in a zh-CN
                        browser and could not be bounded past a whole-minute
                        min/max the browser was free to ignore. */}
                    <button
                      type="button"
                      className="sched-input"
                      style={{ flex: '1 1 200px' }}
                      data-testid="publish-schedule-trigger"
                      aria-label={t('distribution.publish.scheduleAt', 'Scheduled time')}
                      aria-invalid={scheduleIssue !== null}
                      onClick={(e) => setScheduleAnchor(e.currentTarget)}
                    >
                      <Calendar />
                      {scheduledAt.replace('T', ' ')
                        || t('distribution.publish.schedulePick', 'Pick a time')}
                    </button>
                    <DateTimePopover
                      mode="single"
                      withTime
                      quickOptions
                      anchorEl={scheduleAnchor}
                      value={scheduledAt || null}
                      minAt={scheduleWindow.minAt}
                      maxAt={scheduleWindow.maxAt}
                      onChange={(next) => setScheduledAt(next ?? '')}
                      onClose={() => setScheduleAnchor(null)}
                    />
                  </>
                ) : (
                  <span className="sched-input"><Calendar />{t('distribution.publish.notScheduled', 'Not scheduled')}</span>
                )}
              </div>
              {/* The picker cannot offer an out-of-window time, so these two
                  now only fire for a value that aged out while the form sat
                  open. Kept because the window is re-checked in the request
                  schema and again before a browser opens — finding out after a
                  full upload is the failure this whole block exists to stop. */}
              {scheduleIssue === 'tooSoon' && (
                <p className="field-err">
                  {t(
                    'distribution.publish.scheduleTooSoon',
                    'Pick a time at least {{min}} from now — the platform minimum plus room for the upload.',
                    { min: minLeadWords },
                  )}
                </p>
              )}
              {scheduleIssue === 'tooFar' && (
                <p className="field-err">
                  {t(
                    'distribution.publish.scheduleTooFar',
                    'Pick a time within {{max}} from now.',
                    { max: maxAheadWords },
                  )}
                </p>
              )}
              {scheduleIssue === 'empty' && (
                <p className="field-err">
                  {t('distribution.publish.scheduleEmpty', 'Choose when this should publish.')}
                </p>
              )}
            </div>
          </div>

          <div className="fcard">
            <h4>{t('distribution.publish.moreOptions', 'More options')}</h4>
            {/* Collections are matched BY NAME against the ones the account
                already has — we can't enumerate them from here, so this is a
                free-text field and a name that doesn't exist fails the row
                (loudly) rather than publishing outside the collection. */}
            <div className="opt-row">
              <Folder />
              <span className="ol">{t('distribution.publish.collection', 'Collection')}</span>
              <input
                className="input"
                style={{ width: 200 }}
                value={collectionName}
                maxLength={100}
                aria-label={t('distribution.publish.collection', 'Collection')}
                placeholder={t('distribution.publish.collectionPlaceholder', 'Existing collection name')}
                onChange={(e) => setCollectionName(e.target.value)}
              />
            </div>
            {/* Music is picked from the PLATFORM'S OWN catalogue, and what
                travels is the track's id — not its title. A title is not an
                identity: one search for 「起风了」 returns five rows whose
                titles are character-identical under different ids, and the
                publish step refuses (`music_ambiguous`) rather than guess.
                This panel is what makes that refusal avoidable.

                Hidden entirely when the accounts in play have no music picker
                — the same capability read as before. */}
            {musicSupported && (
              <div className="opt-row opt-row-stack">
                {/* The ONE audition element. It lives out here, with the row
                    rather than inside the panel, for two reasons: a single
                    element is what makes "only one track at a time" structural
                    rather than bookkeeping, and closing the panel has to leave
                    something behind that can still be paused. `preload="none"`
                    so opening the panel costs no bytes until a play is asked
                    for. */}
                <audio
                  ref={attachMusicAudio}
                  data-testid="music-preview-audio"
                  preload="none"
                  style={{ display: 'none' }}
                  onEnded={() => {
                    musicPlayingIdRef.current = null;
                    setMusicPlayingId(null);
                  }}
                  onError={() => {
                    // The file did not load. Say so on the row that was
                    // pressed — a play button that goes quiet and stays quiet
                    // tells the user nothing about whose fault it was.
                    const failed = musicPlayingIdRef.current;
                    console.error('distribution: music preview could not be loaded');
                    musicPlayingIdRef.current = null;
                    setMusicPlayingId(null);
                    if (failed) setMusicPreviewFailed(failed);
                  }}
                />
                <div className="opt-row-head">
                  <Music />
                  <span className="ol">{t('distribution.publish.music', 'Music')}</span>
                  {musicTrack ? (
                    <span className="music-chosen" data-testid="music-chosen">
                      <span className="mc-title">{musicTrack.title}</span>
                      <span className="mc-meta">
                        {musicTrack.author || NO_VALUE}
                        {' · '}
                        {formatDuration(musicTrack.duration)}
                      </span>
                      <button
                        type="button"
                        className="mc-clear"
                        aria-label={t('distribution.publish.musicClear', 'Remove music')}
                        onClick={() => {
                          setMusicTrack(null);
                          setMusicName('');
                        }}
                      >
                        <X />
                      </button>
                    </span>
                  ) : (
                    <span className="ov">
                      {t('distribution.publish.musicDefault', 'Original sound')}
                    </span>
                  )}
                  <button
                    type="button"
                    className="oa oa-btn"
                    aria-expanded={musicPanelOpen}
                    data-testid="music-panel-toggle"
                    onClick={() => setMusicPanelOpen((v) => !v)}
                  >
                    {musicPanelOpen
                      ? t('distribution.publish.musicClose', 'Close')
                      : t('distribution.publish.musicChoose', 'Choose')}
                  </button>
                </div>

                {/* Nothing is fetched until this is open: every lookup is paid
                    for with a real account's cookies. */}
                {musicPanelOpen && (
                  <div className="music-panel" data-testid="music-panel">
                    {/* WHOSE library this is. The catalogue is global, but the
                        credential is minted with the first target account's
                        session and the platform's saved-tracks tab is
                        account-scoped — so switching target accounts changes
                        what this panel can show. Stating it is not decoration:
                        a list that changes for reasons the user cannot see is
                        the failure this repo keeps finding. */}
                    <div className="music-identity" data-testid="music-identity">
                      {targetAccounts[0] ? (
                        <>
                          <AccountAvatar
                            gradient={gradientFor(targetAccounts[0].id)}
                            username={musicIdentity?.username ?? targetAccounts[0].username}
                            avatarUrl={
                              musicIdentity?.avatar_url ?? targetAccounts[0].avatar_url
                            }
                          />
                          {/* The account name is rendered as its own node
                              rather than interpolated into the sentence: it is
                              the one part of this line that has to survive
                              however the translation layer is wired. Same rule
                              the topic row's play count follows. */}
                          <span>
                            {t(
                              'distribution.publish.musicBrowsingAs',
                              'Music library of',
                            )}
                            {' '}
                            <b>{musicIdentity?.username ?? targetAccounts[0].username}</b>
                          </span>
                        </>
                      ) : (
                        <span>
                          {t(
                            'distribution.publish.musicNoAccount',
                            'Select an account first — the music library is read with its session.',
                          )}
                        </span>
                      )}
                    </div>

                    <div className="music-search">
                      <Search />
                      <input
                        className="input"
                        value={musicQuery}
                        maxLength={50}
                        aria-label={t('distribution.publish.musicSearch', 'Search music')}
                        placeholder={t('distribution.publish.musicSearch', 'Search music')}
                        onChange={(e) => setMusicQuery(e.target.value)}
                      />
                    </div>

                    {/* Four states, and an empty list is only ever shown for
                        "the platform answered with nothing" — never for a
                        failure. */}
                    {musicState === 'loading' && (
                      <div className="music-note">
                        <Loader2 className="spin" />
                        {t('distribution.publish.musicSearching', 'Searching the platform…')}
                      </div>
                    )}
                    {musicState === 'error' && (
                      <div className="music-note music-error" role="alert" data-testid="music-error">
                        <AlertCircle />
                        {musicErrorText()}
                      </div>
                    )}
                    {musicState === 'ready' && musicResults.length === 0 && (
                      <div className="music-note">
                        {t(
                          'distribution.publish.musicEmpty',
                          'The platform returned nothing for that — try a different wording.',
                        )}
                      </div>
                    )}
                    {/* The tab row. Only when nothing is typed: a search
                        REPLACES the chart, and leaving the tabs highlighted
                        under a result list would claim the results came from
                        the highlighted chart. */}
                    {!musicQuery.trim() && musicCharts.length > 0 && (
                      <div className="music-tabs" role="tablist" data-testid="music-tabs">
                        {musicCharts.map((chart) => {
                          const key = musicChartKey(chart);
                          return (
                            <button
                              key={key}
                              type="button"
                              role="tab"
                              aria-selected={key === activeChartKey}
                              className={`music-tab ${key === activeChartKey ? 'on' : ''}`}
                              data-testid={`music-tab-${key}`}
                              onClick={() => setActiveChartKey(key)}
                            >
                              {chart.category_name}
                            </button>
                          );
                        })}
                      </div>
                    )}

                    {/* Four notes about the CACHE, kept apart because they
                        call for different actions. A cold cache is not a
                        fault; a stale one is not an error; an unreadable one
                        is ours, not the platform's. */}
                    {!musicQuery.trim() && musicChartsState === 'loading' && (
                      <div className="music-note">
                        <Loader2 className="spin" />
                        {t('distribution.publish.musicChartsLoading', 'Loading charts…')}
                      </div>
                    )}
                    {!musicQuery.trim() && musicChartsState === 'error' && (
                      <div className="music-note music-error" role="alert">
                        <AlertCircle />
                        {t(
                          'distribution.publish.musicChartsError',
                          'Could not read the cached charts — search still works.',
                        )}
                      </div>
                    )}
                    {!musicQuery.trim()
                      && musicChartsState === 'ready'
                      && musicChartsMeta?.neverHarvested && (
                      <div className="music-note" data-testid="music-charts-cold">
                        {t(
                          'distribution.publish.musicChartsCold',
                          'No charts read yet. Reading them takes about two minutes and leaves one draft on the account.',
                        )}
                        {' '}
                        <button
                          type="button"
                          className="oa oa-btn"
                          disabled={musicRefreshing}
                          data-testid="music-charts-refresh"
                          onClick={runMusicChartRefresh}
                        >
                          {musicRefreshing
                            ? t('distribution.publish.musicChartsReading', 'Reading…')
                            : t('distribution.publish.musicChartsRead', 'Read charts')}
                        </button>
                      </div>
                    )}
                    {/* An empty chart that was READ is a fact about the
                        account (no saved tracks), not a failure — measured on
                        a real account whose favourites tab is genuinely
                        empty. It must not read as "loading" or "broken". */}
                    {!musicQuery.trim()
                      && musicChartsState === 'ready'
                      && !musicChartsMeta?.neverHarvested
                      && activeChart
                      && activeChart.tracks.length === 0 && (
                      <div className="music-note" data-testid="music-chart-empty">
                        {activeChart.ok
                          ? t(
                            'distribution.publish.musicChartEmpty',
                            'This list is empty on the platform.',
                          )
                          : t(
                            'distribution.publish.musicChartUnread',
                            'This list could not be read last time.',
                          )}
                      </div>
                    )}
                    {musicQuery.trim() && musicState === 'idle' && (
                      <div className="music-note">
                        {t(
                          'distribution.publish.musicPrompt',
                          'Type a track or artist to search the platform.',
                        )}
                      </div>
                    )}
                    {visibleTracks.length > 0 && (
                      <div className="music-results" role="listbox">
                        {visibleTracks.map((track) => {
                          const previewUrl = musicPreviewUrl(track);
                          const playing = musicPlayingId === track.music_id;
                          const loadingPreview = musicPreviewLoading === track.music_id;
                          const pick = () => {
                            setMusicTrack(track);
                            // The keyword the browser will type into the
                            // platform's own dialog. Derived from the track,
                            // never typed independently: two sources of truth
                            // here means the search cannot find the pick.
                            setMusicName(track.title);
                            setMusicPanelOpen(false);
                          };
                          return (
                            /* A div, not a button. The row now holds a second
                               control (audition), and a button inside a button
                               is invalid HTML that browsers resolve by
                               dropping one of them. `role="option"` +
                               tabIndex + the key handler keep exactly the
                               keyboard and a11y contract the <button> had. */
                            <div
                              key={track.music_id}
                              role="option"
                              tabIndex={0}
                              aria-selected={musicTrack?.music_id === track.music_id}
                              className={`music-row ${musicTrack?.music_id === track.music_id ? 'sel' : ''}`}
                              /* The id is the identity — carried on the node so
                                 a test (and a human in devtools) can see WHICH
                                 row was taken, not merely that a row with that
                                 title was. */
                              data-music-id={track.music_id}
                              onClick={pick}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  pick();
                                }
                              }}
                            >
                              {track.cover_url ? (
                                <img className="mr-cover" src={track.cover_url} alt="" loading="lazy" />
                              ) : (
                                <span className="mr-cover mr-cover-empty" aria-hidden="true" />
                              )}
                              <span className="mr-main">
                                <span className="mr-title">{track.title}</span>
                                <span className="mr-meta">
                                  {track.author || NO_VALUE}
                                  {' · '}
                                  {formatDuration(track.duration)}
                                </span>
                              </span>
                              {/* ⚠️ `null` is NOT zero. Zero is a real
                                  catalogue value — a track nobody uses — and
                                  `null` means the platform did not say. This
                                  has to be an explicit branch because the
                                  type system does not enforce it here:
                                  `tsconfig.json` sets no `strictNullChecks`,
                                  so `formatViewCount(n: number)` accepts a
                                  null without complaint and would render an
                                  invented "0 uses". Pinned by a test, since
                                  the compiler cannot be. */}
                              <span className="mr-uses">
                                {track.user_count === null ? (
                                  <b>{NO_VALUE}</b>
                                ) : (
                                  <>
                                    <b>{formatViewCount(track.user_count, i18n.language)}</b>
                                    {' '}
                                    {t('distribution.publish.musicUses', 'uses')}
                                  </>
                                )}
                              </span>
                              {/* Two outcomes, never one dead button. A row the
                                  catalogue gave no playable file for says so
                                  and is not pressable; only a row we can
                                  actually sound gets a control. */}
                              {previewUrl ? (
                                <button
                                  type="button"
                                  className={`mr-preview ${playing ? 'on' : ''} ${loadingPreview ? 'loading' : ''}`}
                                  data-testid={`music-preview-${track.music_id}`}
                                  aria-pressed={playing}
                                  /* The audition is downloaded in full before
                                     it can sound (see `fetchMusicPreview`), so
                                     there is a real interval where the button
                                     is neither idle nor playing. Saying
                                     "Loading" is what keeps that interval from
                                     reading as a control that did nothing. */
                                  aria-busy={loadingPreview}
                                  aria-label={
                                    loadingPreview
                                      ? t('distribution.publish.musicPreviewLoading', 'Loading preview')
                                      : playing
                                        ? t('distribution.publish.musicPreviewStop', 'Stop preview')
                                        : t('distribution.publish.musicPreviewPlay', 'Preview track')
                                  }
                                  title={
                                    loadingPreview
                                      ? t('distribution.publish.musicPreviewLoading', 'Loading preview')
                                      : playing
                                        ? t('distribution.publish.musicPreviewStop', 'Stop preview')
                                        : t('distribution.publish.musicPreviewPlay', 'Preview track')
                                  }
                                  onClick={(e) => {
                                    // The row behind this selects the track;
                                    // auditioning one is not choosing it.
                                    e.stopPropagation();
                                    void toggleMusicPreview(track);
                                  }}
                                >
                                  {loadingPreview
                                    ? <Loader2 className="spin" />
                                    : playing ? <Pause /> : <Play />}
                                </button>
                              ) : (
                                <span
                                  className="mr-preview mr-preview-none"
                                  data-testid={`music-preview-none-${track.music_id}`}
                                  title={t(
                                    'distribution.publish.musicPreviewUnavailable',
                                    'No preview for this track',
                                  )}
                                >
                                  {t('distribution.publish.musicPreviewUnavailableShort', 'No preview')}
                                </span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {/* A preview that refused to play says so. Silence with no
                        note is indistinguishable from a track that is simply
                        quiet at the start, and leaves the user pressing again. */}
                    {musicPreviewFailed && (
                      <div
                        className="music-note music-error"
                        role="alert"
                        data-testid="music-preview-error"
                      >
                        <AlertCircle />
                        {t(
                          'distribution.publish.musicPreviewFailed',
                          'That preview would not play — the platform\u2019s file did not load. Picking the track still works.',
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
            {/* The row above is hidden by two very different facts. Say which:
                "this platform has no music picker" is the platform's answer and
                needs no note, "we never got an answer" is ours. */}
            {!musicSupported && capabilitiesUnavailable && (
              <p className="hint" style={{ margin: '4px 0 0' }}>
                {t(
                  'distribution.publish.musicCapabilityUnknown',
                  'Music is hidden because we could not read what the connected platforms support.',
                )}
              </p>
            )}
            <div className="opt-row">
              <MapPin />
              <span className="ol">{t('distribution.publish.location', 'Location')}</span>
              <span className="oa">{t('distribution.publish.add', 'Add')}</span>
            </div>
            <div className="opt-row">
              <TrendingUp />
              <span className="ol">{t('distribution.publish.trendingTopic', 'Trending topic')}</span>
              <span className="ov">{t('distribution.publish.trendingTopicDesc', 'Link a rising topic for extra reach')}</span>
              <span className="oa">{t('distribution.publish.link', 'Link')}</span>
            </div>
            <div className="opt-row">
              <ListOrdered />
              <span className="ol">{t('distribution.publish.chapters', 'Chapters')}</span>
              <span className="ov">{t('distribution.publish.chaptersDesc', 'Where the platform supports them')}</span>
              <span className="oa">{t('distribution.publish.add', 'Add')}</span>
            </div>
          </div>

          {!isImages && (
            <div className="fcard">
              <h4>
                {t('distribution.publish.distributionMode', 'Distribution mode')}
                <span className="aux">
                  {t('distribution.publish.videosAccountsCount', '{{v}} videos × {{a}} accounts', { v: selectedVideos.length, a: selectedAccounts.length })}
                </span>
              </h4>
              <div className="mode-cards">
                <button type="button" className={`mode ${mode === 'broadcast' ? 'on' : ''}`} onClick={() => setMode('broadcast')}>
                  <b><Radio /> {t('distribution.publish.mode_broadcast', 'Broadcast')}</b>
                  <span>{t('distribution.publish.broadcastDesc', 'Every account posts every video — {{n}} posts total.', { n: postsBroadcast })}</span>
                </button>
                <button type="button" className={`mode ${mode === 'one_to_one' ? 'on' : ''}`} onClick={() => setMode('one_to_one')}>
                  <b><ArrowLeftRight /> {t('distribution.publish.mode_one_to_one', 'One-to-one')}</b>
                  <span>{t('distribution.publish.oneToOneDesc', 'Videos are assigned round-robin — {{n}} posts total.', { n: postsOneToOne })}</span>
                </button>
              </div>
            </div>
          )}
        </div>

        {/* ── right: rail ── */}
        <div className="pub-rail">
          {/* The orientation state finally does something. It has been on this
              page since the card was first stubbed in, wired to nothing but
              its own highlight — a switch the user could flip that changed
              nothing on screen. It now chooses which derived crop the grid is
              drawn with. */}
          <PublishPreview
            kind={contentType}
            items={selectedVideoObjs}
            title={title}
            handle={previewHandle}
            avatarUrl={firstSelectedAccount?.avatar_url ?? null}
            platform={firstSelectedAccount?.platform ?? null}
            covers={covers}
            soundtrack={previewSoundtrack}
            mediaToken={mediaToken}
            orientation={orientation}
            onOrientationChange={setOrientation}
          />

          <div className="fcard">
            <h4>
              {t('distribution.publish.publishTo', 'Publish to')}
              <span className="aux">
                {t('distribution.publish.selectedOfTotal', '{{selected}} of {{total}} selected', { selected: selectedAccounts.length, total: accounts.length })}
              </span>
            </h4>
            {/* The channel picker used to live here. It was removed rather than
                extended with a third button: a channel only works for accounts
                bound the matching way, so offering the choice mostly offered a
                way to pick a broken combination. Each row now states how that
                account will publish. */}
            <p className="hint" style={{ marginBottom: 10 }}>
              {t(
                'distribution.publish.routeHint',
                'Each account publishes the way it was connected — shown on its row.',
              )}
            </p>

            {accounts.map((a) => {
              /* Both dead statuses, not just the OAuth one. `expired` is a
                 lapsed OAuth token; a QR-bound session never reaches it — it
                 dies as `needs_relogin`. Checking only `expired` here let a
                 session-bound user (i.e. everyone publishing unattended) tick
                 an offline account and find out at publish time. The predicate
                 is shared with AccountsPage so the two cannot drift again. */
              const blocked = needsReconnect(a);
              /* Two statuses, two recoveries, two sentences — the same words
                 AccountsPage uses on the card the user is being sent to.
                 Merging them would send half of them to the wrong button. */
              const blockedLabel = a.status === 'needs_relogin'
                ? t('distribution.publish.reloginRescan', 'Signed out — scan again')
                : t('distribution.publish.expiredReauthorize', 'Expired — reauthorize');
              const on = selectedAccounts.includes(a.id);
              const badge = PLATFORM_BADGE[a.platform];
              const open = customizeOpen[a.id];
              return (
                <React.Fragment key={a.id}>
                  <div
                    role="checkbox"
                    aria-checked={on}
                    aria-disabled={blocked}
                    tabIndex={blocked ? -1 : 0}
                    className={`acct-row ${on ? 'sel' : ''} ${blocked ? 'dis' : ''}`}
                    onClick={() => onToggleAccount(a.id, blocked)}
                    onKeyDown={(e) => {
                      if (!blocked && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault();
                        onToggleAccount(a.id, blocked);
                      }
                    }}
                  >
                    <span className="ck" />
                    <AccountAvatar
                      gradient={gradientFor(a.id)}
                      username={a.username}
                      avatarUrl={a.avatar_url}
                    >
                      {badge && (
                        <span className="pbadge sm" style={{ background: badge.bg }}>{badge.icon}</span>
                      )}
                    </AccountAvatar>
                    <span className="nm">
                      {a.username}
                      <small>
                        {PLATFORM_LABEL[a.platform] ?? a.platform}
                        {' · '}
                        {blocked
                          ? blockedLabel
                          : (a.scope_type === 'team' ? t('distribution.teamScope', 'Team') : t('distribution.personalScope', 'Personal'))}
                        {' · '}
                        {/* How this row will actually publish. Worth stating: the
                            two routes differ in whether the user has to do
                            anything after pressing Publish, and that is the whole
                            reason someone binds by QR code. */}
                        {a.auth_type === 'session'
                          ? t('distribution.publish.routeUnattended', 'Publishes unattended')
                          : t('distribution.publish.routeNeedsPhone', 'Needs confirming on your phone')}
                      </small>
                    </span>
                    {!blocked && (
                      <button
                        type="button"
                        className="cust"
                        onClick={(e) => {
                          e.stopPropagation();
                          setCustomizeOpen((s) => ({ ...s, [a.id]: !s[a.id] }));
                        }}
                      >
                        {t('distribution.publish.customize', 'Customize')} {open ? '▾' : '▸'}
                      </button>
                    )}
                  </div>
                  {!blocked && open && (
                    <div className="override">
                      <label htmlFor={`override-title-${a.id}`}>{t('distribution.publish.titleForAccount', 'Title for this account')}</label>
                      <input
                        id={`override-title-${a.id}`}
                        className="input"
                        value={accountConfigs[a.id]?.title ?? ''}
                        placeholder={title}
                        onChange={(e) => onAccountTitleChange(a.id, e.target.value)}
                      />
                    </div>
                  )}
                </React.Fragment>
              );
            })}
            {accounts.length === 0 && (
              <p className="text-[12px]" style={{ color: 'var(--content-4)' }}>
                {t('distribution.publish.noAccounts', 'Connect an account first')}
              </p>
            )}
          </div>

          <div className="summary">
            <h4>{t('distribution.publish.summaryHeading', 'Summary')}</h4>
            <div className="line">
              <span>{isImages ? t('distribution.publish.imagesLabel', 'Images') : t('distribution.publish.videosLabel', 'Videos')}</span>
              <b>{selectedVideos.length}</b>
            </div>
            <div className="line"><span>{t('distribution.publish.accountsLabel', 'Accounts')}</span><b>{selectedAccounts.length}</b></div>
            {!isImages && (
              <div className="line"><span>{t('distribution.publish.modeLabel', 'Mode')}</span><b>{mode === 'broadcast' ? t('distribution.publish.mode_broadcast', 'Broadcast') : t('distribution.publish.mode_one_to_one', 'One-to-one')}</b></div>
            )}
            <div className="line total"><span>{t('distribution.publish.postsToCreate', 'Posts to create')}</span><b>{totalPosts}</b></div>

            {/* A missing cover is not an error — the platform picks its own
                frame. It IS worth saying, because the frame it picks is
                usually the first one. */}
            {covers ? (
              <div className="check ok">
                <Check strokeWidth={2.5} />
                {t('distribution.publish.coverSetOk', 'Cover set — vertical and horizontal crops attached.')}
              </div>
            ) : (
              <div className="check warn">
                <AlertTriangle />
                {isImages
                  ? t('distribution.publish.coverImagesNote', 'The first image is used as the cover for image posts.')
                  : t('distribution.publish.coverNotSetWarning', 'No cover set — the platform picks a frame during publish.')}
              </div>
            )}
            {/* Scheduling / declarations / collections only exist on the creator
                page, which only a QR-bound account reaches. Those rows fail
                rather than publish with the field dropped — so say it here,
                while the selection can still be changed. */}
            {usesCreatorPageOnlyFields && nonSessionSelected > 0 && (
              <div className="check warn">
                <AlertTriangle />
                {t(
                  'distribution.publish.creatorPageOnlyWarning',
                  'Scheduling, self declaration and collections need accounts connected by QR code — {{n}} selected account(s) will fail.',
                  { n: nonSessionSelected },
                )}
              </div>
            )}
            {/* Typed refusals from the submit-time gate (422). One line per
                problem, because a batch is refused per account and "which
                account made this fail" is the only thing the user can act on.
                Never the backend's own English `message` — see
                `gateProblemText`. */}
            {gateProblems.length > 0 && (
              <div className="check err" role="alert">
                <AlertCircle />
                <span>
                  <b>{t('distribution.publish.rejectedHeading', 'This post was refused before anything was created:')}</b>
                  <ul className="gate-problems">
                    {gateProblems.map((p, i) => (
                      <li key={`${p.reason}-${p.account_id ?? 'batch'}-${i}`}>{gateProblemText(p)}</li>
                    ))}
                  </ul>
                </span>
              </div>
            )}
            {canPublish && gateProblems.length === 0 && (
              <div className="check ok">
                <Check strokeWidth={2.5} />
                {t('distribution.publish.lookingGood', 'Title, topics and accounts look good.')}
              </div>
            )}

            <div className="actions">
              <button type="button" className="btn btn-ghost" disabled title={t('distribution.comingInD3', 'Coming in D3')}>{t('distribution.publish.saveDraft', 'Save draft')}</button>
              <button type="button" className="btn btn-solid" disabled={!canPublish || submitting} onClick={onPublish}>
                <Send size={15} /> {t('distribution.publish.publishNow', 'Publish now')}
              </button>
            </div>

            {channel === 'h5' && (
              <div className="handoff">
                <AlertCircle />
                {t('distribution.publish.handoffMsg', 'Douyin personal accounts finish inside the Douyin app — we hand off automatically and track the result here.')}
              </div>
            )}
          </div>
        </div>
      </div>

      {pickerOpen && (
        <div
          className="picker-overlay"
          role="presentation"
          onClick={() => setPickerOpen(false)}
        >
          <div
            className="picker"
            role="dialog"
            aria-modal="true"
            aria-label={t('distribution.publish.pickerTitle', 'Add from Library')}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="picker-head">
              <div>
                <h3>{t('distribution.publish.pickerTitle', 'Add from Library')}</h3>
                {/* The picker is shared by both content types, so its copy has
                    to follow the mode — an images-mode dialog that says
                    "videos" everywhere describes a different feature. */}
                <p>
                  {isImages
                    ? t('distribution.publish.pickerSubtitleImages', 'Pick images to include in this post.')
                    : t('distribution.publish.pickerSubtitle', 'Pick videos to include in this publish.')}
                </p>
              </div>
              <div className="picker-count">
                {t('distribution.publish.pickerSelectedCount', '{{n}} selected', { n: selectedVideos.length })}
              </div>
              <button
                type="button"
                className="picker-close"
                aria-label={t('distribution.publish.pickerClose', 'Close')}
                onClick={() => setPickerOpen(false)}
              >
                <X size={16} />
              </button>
            </div>

            <div className="picker-search">
              <Search size={14} />
              <input
                value={pickerQuery}
                onChange={(e) => setPickerQuery(e.target.value)}
                placeholder={pickerSearchLabel}
                aria-label={pickerSearchLabel}
              />
            </div>

            <div className="picker-tabs">
              <div className="seg">
                <button
                  type="button"
                  className={pickerTab === 'library' ? 'on' : ''}
                  onClick={() => setPickerTab('library')}
                >
                  {t('distribution.publish.pickerTabLibrary', 'Library')}
                </button>
                {/* Generated media is video-only — hide the tab for images. */}
                {!isImages && (
                  <button
                    type="button"
                    className={pickerTab === 'generated' ? 'on' : ''}
                    onClick={() => setPickerTab('generated')}
                  >
                    {t('distribution.publish.pickerTabGenerated', 'Generated')}
                  </button>
                )}
              </div>
              {pickerTab === 'library' && (
                <button
                  type="button"
                  className={`chip ${toPublishOnly ? 'chip-indigo' : 'chip-mute'}`}
                  aria-pressed={toPublishOnly}
                  onClick={() => setToPublishOnly((v) => !v)}
                >
                  <Bookmark size={11} />
                  {t('distribution.publish.pickerToPublishOnly', 'To publish')}
                </button>
              )}
            </div>

            {pickerTab === 'library' && (
              <div className="picker-grid">
                {pickerResults.map((v) => {
                  // Gallery entity — its own card that expands into child images
                  // on pick. `on` reflects the whole group being selected.
                  if (isGalleryRow(v)) {
                    const childIds = galleryChildren[v.id];
                    const galOn = Boolean(childIds && childIds.length > 0
                      && childIds.every((id) => selectedVideos.includes(id)));
                    const busy = expandingGalleryId === v.id;
                    const count = v.gallery_count ?? childIds?.length ?? 0;
                    const galImg = Boolean(v.thumbnail_url);
                    return (
                      <button
                        type="button"
                        key={v.id}
                        className={`picker-item ${galOn ? 'sel' : ''}`}
                        aria-pressed={galOn}
                        disabled={busy}
                        onClick={() => void onPickGallery(v)}
                      >
                        <span
                          className={`pi-thumb ${galImg ? '' : 'ph'} ${busy ? 'pulse' : ''}`}
                          style={galImg ? { backgroundImage: `url(${v.thumbnail_url})` } : undefined}
                        >
                          <span className="pi-gallery-badge">
                            <Images size={11} strokeWidth={2.5} />
                            {count}
                          </span>
                          {busy && (
                            <span className="pi-busy"><Loader2 size={16} className="spin" /></span>
                          )}
                          {galOn && !busy && (
                            <span className="pi-check"><Check size={12} strokeWidth={3} /></span>
                          )}
                        </span>
                        <span className="pi-name" title={v.filename}>{v.filename}</span>
                      </button>
                    );
                  }
                  const on = selectedVideos.includes(v.id);
                  const marked = markedIds.has(v.id);
                  const hasImg = Boolean(v.thumbnail_url);
                  const previewing = previewId === v.id;
                  // Built once per card so the title attribute (hover) and the
                  // visible line can never drift apart.
                  const meta = [
                    formatResolution(v.resolution),
                    formatBytes(v.file_size_bytes),
                    formatShortDate(v.created_at, i18n.language),
                  ];
                  return (
                    <button
                      type="button"
                      key={v.id}
                      className={`picker-item ${on ? 'sel' : ''}`}
                      aria-pressed={on}
                      onClick={() => setSelectedVideos((s) => toggle(s, v.id))}
                    >
                      <span
                        className={`pi-thumb ${hasImg ? '' : 'ph'}`}
                        style={hasImg && !previewing
                          ? { backgroundImage: `url(${v.thumbnail_url})` }
                          : undefined}
                      >
                        {previewing ? (
                          // Inline, in the tile it replaces — a separate modal
                          // would hide the very grid the user is comparing
                          // against. No autoPlay: a preview that starts making
                          // noise on its own is a worse default than one click.
                          <video
                            className="pi-video"
                            src={getResourceFileUrl(v.id, mediaToken)}
                            controls
                            preload="metadata"
                            onClick={(e) => e.stopPropagation()}
                          />
                        ) : (
                          <>
                            <span
                              role="button"
                              tabIndex={0}
                              className={`pi-mark ${marked ? 'on' : ''}`}
                              aria-label={marked
                                ? t('distribution.publish.pickerUnmark', 'Unmark to publish')
                                : t('distribution.publish.pickerMark', 'Mark to publish')}
                              title={marked
                                ? t('distribution.publish.pickerUnmark', 'Unmark to publish')
                                : t('distribution.publish.pickerMark', 'Mark to publish')}
                              onClick={(e) => { e.stopPropagation(); void onToggleMark(v.id); }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  void onToggleMark(v.id);
                                }
                              }}
                            >
                              <Bookmark size={11} strokeWidth={2.5} />
                            </span>
                            <span
                              role="button"
                              tabIndex={0}
                              className="pi-play"
                              aria-label={t('distribution.publish.pickerPreview', 'Preview video')}
                              title={t('distribution.publish.pickerPreview', 'Preview video')}
                              onClick={(e) => { e.stopPropagation(); setPreviewId(v.id); }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  setPreviewId(v.id);
                                }
                              }}
                            >
                              <Play size={12} strokeWidth={2.5} />
                            </span>
                            <span className="pi-dur">{formatDuration(v.duration_seconds)}</span>
                            {on && (
                              <span className="pi-check"><Check size={12} strokeWidth={3} /></span>
                            )}
                          </>
                        )}
                      </span>
                      <span className="pi-name" title={v.filename}>{v.filename}</span>
                      {/* Resolution · size · date — the line that tells two cuts
                          of the same content apart. Em dashes where we have no
                          value; nothing here is inferred. */}
                      <span className="pi-meta" title={meta.join(' · ')}>
                        {meta.map((part, idx) => (
                          <React.Fragment key={part + String(idx)}>
                            {idx > 0 && <span className="pi-dot" aria-hidden="true">·</span>}
                            <span>{part}</span>
                          </React.Fragment>
                        ))}
                      </span>
                      {previewing && (
                        <span
                          role="button"
                          tabIndex={0}
                          className="pi-close-preview"
                          onClick={(e) => { e.stopPropagation(); setPreviewId(null); }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              e.stopPropagation();
                              setPreviewId(null);
                            }
                          }}
                        >
                          {t('distribution.publish.pickerClosePreview', 'Close preview')}
                        </span>
                      )}
                    </button>
                  );
                })}
                {pickerResults.length === 0 && (
                  <p className="picker-empty">
                    {videos.length === 0
                      ? (isImages
                        ? t('distribution.publish.noImages', "No uploaded images yet — downloads aren't publishable")
                        : t('distribution.publish.noContent', "No uploads or generated videos yet — downloads aren't publishable"))
                      : toPublishOnly && markedIds.size === 0
                        ? (isImages
                          ? t('distribution.publish.pickerNoMarkedImages', 'No images marked to publish yet')
                          : t('distribution.publish.pickerNoMarked', 'No videos marked to publish yet'))
                        : (isImages
                          ? t('distribution.publish.pickerNoResultsImages', 'No images match your search')
                          : t('distribution.publish.pickerNoResults', 'No videos match your search'))}
                  </p>
                )}
              </div>
            )}

            {pickerTab === 'generated' && (
              <div className="picker-grid">
                {generatedResults.map((g) => {
                  const resourceId = genResourceIds[g.id];
                  const on = resourceId ? selectedVideos.includes(resourceId) : false;
                  const busy = promotingId === g.id;
                  const name = g.name || t('distribution.publish.generatedUntitled', 'Generated video');
                  return (
                    <button
                      type="button"
                      key={g.id}
                      className={`picker-item ${on ? 'sel' : ''}`}
                      aria-pressed={on}
                      disabled={busy}
                      onClick={() => void onPickGenerated(g)}
                    >
                      <span className={`pi-thumb ph gen ${busy ? 'pulse' : ''}`}>
                        <span className="pi-gen-badge"><Sparkles size={10} /></span>
                        {on && (
                          <span className="pi-check"><Check size={12} strokeWidth={3} /></span>
                        )}
                      </span>
                      <span className="pi-name" title={name}>{name}</span>
                    </button>
                  );
                })}
                {generatedResults.length === 0 && (
                  <p className="picker-empty">
                    {t('distribution.publish.pickerNoGenerated', 'No generated videos yet')}
                  </p>
                )}
              </div>
            )}

            <div className="picker-foot">
              <button type="button" className="btn btn-solid" onClick={() => setPickerOpen(false)}>
                {t('distribution.publish.pickerDone', 'Done')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PublishPage;
