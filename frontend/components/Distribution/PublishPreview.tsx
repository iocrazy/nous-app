/**
 * "What will this look like once it is out there" — the publish page's right
 * rail, inside a phone shell.
 *
 * ══ THE ONE RULE THIS FILE EXISTS TO KEEP ══════════════════════════════════
 *
 * Everything on screen is either the user's own material or an explicitly
 * labelled placeholder. Nothing is invented and dressed up as fact.
 *
 * The card this replaces broke that rule three times over, and each one is
 * worth naming because they are the shapes that grow back:
 *
 *   1. A like count and a comment count, both rendered as `0`. A post that has
 *      not been published does not have zero likes — it has no likes, which is
 *      a different statement. `0` reads as a measurement.
 *   2. "Original sound · @handle", asserted unconditionally. The page has a
 *      music panel; the moment a track is picked that line is simply false.
 *   3. A hardcoded purple gradient standing in for the video frame, with no
 *      indication that it was not the user's content.
 *
 * All four glyphs went with the mock-up they decorated. If a later batch wants
 * the heart / comment / share icons back as scenery, they come back
 * `aria-hidden` and WITHOUT numbers beside them: the icon says "this is a feed
 * post", a number claims a measurement.
 *
 * The feed view follows the same rule from the other side: every card around
 * the user's own is an EMPTY placeholder, deliberately. They are not a sample
 * of the real feed and must never be wired to one — the point of the view is
 * how one cover and one title read at that size, and filling the neighbours
 * with real posts would both mislead and bury the thing being looked at.
 *
 * ⚠️ This view was first built as a three-column profile grid. That was the
 * wrong reference entirely: the screen being reproduced is the two-column
 * recommendation FEED, which three details give away — a feed tab row at the
 * top, the app's bottom navigation, and an author name on every card. A
 * profile grid has none of those (every post there is already yours). The
 * cards are two-up, and the user's own is the left card of the first fully
 * visible row, with the row above it cut off the way a mid-scroll feed is.
 *
 * ══ WHEN THE PLAYER STOPS, AND WHAT THAT RULE HANGS OFF ═════════════════════
 *
 * Sound the user can no longer see the source of is sound the user cannot
 * stop. So playback ends when the player leaves the screen (another tab), when
 * it is pointed at a different clip, and when the panel unmounts.
 *
 * ⚠️ The rule keys on `playingSourceId` — the id of the clip the player is
 * pointed at, a PRIMITIVE — and on nothing else. It must never be keyed on the
 * `items` array's identity. The parent rebuilds that array on every render
 * that touches the Library list, so an identity-keyed rule fires for reasons
 * the user did not cause: this page shipped exactly that bug on the music
 * panel, where a background refresh that changed nothing on screen cut off the
 * track the user was listening to. "The array object is new" is not the rule;
 * "the clip being played changed" is.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle, ChevronLeft, ChevronRight, Heart, ImageOff, SlidersHorizontal,
} from 'lucide-react';

import { getResourceFileUrl } from '../../services/resourceService';
import type { CoverPair } from './CoverPicker';

/** The three views the panel offers. */
export type PreviewTab = 'video' | 'gallery' | 'cover';

/** Which of the two derived crops the grid is drawn with. */
export type CoverOrientation = 'vertical' | 'horizontal';

/** The subset of a Library row this panel needs. Structurally satisfied by
 *  `LibraryVideo`, kept narrow so the panel cannot start depending on fields
 *  that are nullable in practice. */
export interface PreviewMedia {
  id: string;
  filename: string;
  thumbnail_url: string | null;
}

export interface PublishPreviewProps {
  /** Video post or image post — decides which tabs can be opened at all. */
  kind: 'video' | 'images';
  /** The selected media, in publish order. Empty is a normal state, not an
   *  error: the panel says there is nothing to show rather than drawing
   *  something that is not there. */
  items: PreviewMedia[];
  /** The title as typed so far. Empty renders as a labelled placeholder. */
  title: string;
  /**
   * The account the post is attributed to, or null when none is selected.
   *
   * Null renders as nothing at all. It used to render as `@yourhandle` — a
   * handle nobody owns, shown in the position where the real one goes, which
   * is the same defect as a fabricated like count wearing different clothes.
   */
  handle: string | null;
  /**
   * The account's avatar, or null when it has none.
   *
   * Null draws nothing. A grey circle in the avatar's place is a picture of an
   * account that does not look like that — small, but the same category of
   * invention as a fabricated like count.
   */
  avatarUrl: string | null;
  /**
   * Which platform the post is going to, from the first selected account, or
   * null when none is selected.
   *
   * This gates the platform chrome. We have been shown what Douyin's feed
   * looks like and nothing else, so Douyin is the only value that draws it —
   * for `kuaishou`, `xiaohongshu` or null the cards render on a bare screen
   * rather than inside an interface we would be making up.
   */
  platform: string | null;
  /** Derived cover crops, or null when the user has not made any. */
  covers: CoverPair | null;
  /** Supabase JWT for `<img src>` / `<video src>` against the file endpoint,
   *  which takes it as `?token=` because a media element cannot send a header.
   *  Undefined while the session is still being read. */
  mediaToken?: string;
  orientation: CoverOrientation;
  onOrientationChange: (next: CoverOrientation) => void;
}

/** Why a tab cannot be opened, or null when it can. */
type TabBlock = 'wrongKind' | null;

/**
 * Where the image in the first grid cell came from.
 *
 * Carried explicitly rather than inferred at render time so the caption under
 * the grid cannot drift from the picture above it: a thumbnail shown because
 * no cover was derived means something different to the user ("the platform
 * will pick its own frame") than a cover they chose, and the difference has to
 * be stated, not left to be guessed from context.
 */
type CoverProvenance = 'cover' | 'firstImage' | 'thumbnail' | 'none';

/**
 * Enough cards to overflow the screen in either orientation, so the feed
 * always reaches the bottom edge and gets clipped there — the way a feed you
 * have not finished scrolling looks.
 *
 * NOT a claim that this many posts exist. It is a fill count: horizontal cards
 * are the shorter ones, so the number is chosen against those, and the extra
 * rows a vertical layout does not need are simply clipped away.
 */
const FEED_CARDS = 12;

/**
 * Where the user's own card sits: left column, first FULLY visible row.
 *
 * Two columns means index 2 is the left card of the second row, and the feed
 * is offset upwards so row 0 is half cut off at the top — which is what the
 * platform's feed actually looks like mid-scroll, and where the user's post
 * was observed sitting. The old three-column layout put it at index 0 because
 * it was modelled on the profile grid; that was the wrong view entirely.
 */
const MINE_INDEX = 2;

/**
 * The platform's own chrome, reproduced verbatim.
 *
 * ⚠️ WHY THIS IS NOT IN i18n, AND WHY IT IS NOT IN ENGLISH.
 *
 * The house rule is that UI text is English and goes through i18n. That rule
 * governs text WE author. These strings are not ours — they are a depiction of
 * a third party's screen, in the same category as a screenshot.
 *
 * Translating them would be the very thing this component exists to stop.
 * Douyin's app does not say "Featured / Following / For You" to the users this
 * preview is for; writing that here would put invented words in a real app's
 * mouth and present the invention as what the user will see. Accuracy and the
 * style guide point in opposite directions, and accuracy wins, because the
 * whole value of the panel is that what it shows is true.
 *
 * The narrowness of the exemption is the safeguard: these are decoration
 * (`aria-hidden`, no buttons, no counts), they are never translated, they are
 * never mixed into our own copy, and — see `showsPlatformChrome` — they are
 * drawn ONLY when the post is actually going to Douyin. Rendering Douyin's
 * chrome around a Xiaohongshu post would be a fabrication of a different
 * app's interface, which is the same defect wearing a different hat.
 */
const DOUYIN_CHROME = {
  feedTabs: ['精选', '关注', '推荐'],
  /* The two-column card feed is the 精选 tab. If the reference shows a
     different tab underlined, this is the one word to change. */
  currentTab: '精选',
  navItems: ['首页', '朋友', '＋', '消息', '我'],
} as const;

export const PublishPreview: React.FC<PublishPreviewProps> = ({
  kind, items, title, handle, avatarUrl, platform, covers, mediaToken,
  orientation, onOrientationChange,
}) => {
  const { t } = useTranslation();

  const [tab, setTab] = useState<PreviewTab>('cover');
  const [galleryIndex, setGalleryIndex] = useState(0);
  /**
   * URLs whose `<img>` fired `error`, so the cell can say so instead of
   * rendering as an empty box.
   *
   * Keyed by URL rather than by media id: a token arriving late changes the
   * URL, and that retry deserves a fresh verdict rather than inheriting the
   * failure of the unsigned attempt before it.
   */
  const [failedUrls, setFailedUrls] = useState<Set<string>>(new Set());
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const markFailed = (url: string) => setFailedUrls((prev) => {
    if (prev.has(url)) return prev;
    const next = new Set(prev);
    next.add(url);
    return next;
  });

  const isImages = kind === 'images';
  /** See DOUYIN_CHROME: only the platform we have actually seen. */
  const showsPlatformChrome = platform === 'douyin';

  const blockFor = (which: PreviewTab): TabBlock => {
    if (which === 'cover') return null;
    if (which === 'video') return isImages ? 'wrongKind' : null;
    return isImages ? null : 'wrongKind';
  };

  /**
   * ⚠️ A PRIMITIVE, and that is the whole point.
   *
   * The parent rebuilds the selection array on every render that touches the
   * Library list, so an effect keyed on `items` identity would fire for
   * reasons the user did not cause — and the page has already been bitten by
   * exactly that: the music audition's stop rule hung off the results array's
   * identity, so a refresh that changed nothing visible cut off the track the
   * user was listening to. Keying on the joined id list states the real rule
   * ("the selection changed") instead of using object identity as a proxy for
   * it, so a rebuild holding the same rows leaves the reader's page alone.
   */
  const itemsKey = useMemo(() => items.map((i) => i.id).join(','), [items]);

  // A changed selection invalidates the page number; an identical one does not.
  useEffect(() => { setGalleryIndex(0); }, [itemsKey]);

  // Switching post type can strand the reader on a tab that no longer applies.
  useEffect(() => {
    setTab((current) => (blockFor(current) === null ? current : 'cover'));
    // `isImages` is the only input to `blockFor` that can change here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isImages]);

  /** Clamped rather than trusted: the selection can shrink under the reader. */
  const pageIndex = items.length === 0
    ? 0
    : Math.min(galleryIndex, items.length - 1);
  const currentItem = items[pageIndex] ?? null;

  /**
   * The clip the player is pointed at, or null whenever no player is on
   * screen. See the "when the player stops" note at the top of this file: this
   * primitive is the ENTIRE stop rule, and it is derived rather than stored so
   * it cannot drift from what is rendered.
   */
  const playingSourceId = tab === 'video' && !isImages ? currentItem?.id ?? null : null;

  useEffect(() => {
    /* ⚠️ `el` is captured at set-up, ON PURPOSE.
       React updates refs during commit and runs passive cleanups after, so by
       the time this cleanup runs the ref already points at the NEW element —
       or at null. Reading `videoRef.current` here would pause the wrong
       element, or nothing at all. (Verified: swapping the capture for a ref
       read turns five of the tests in this file red.)

       Why pause explicitly at all, given that today every one of these cases
       also unmounts the element, and the HTML spec's "removed from a Document"
       steps pause a detached media element? Measured in Chromium 1228, a
       detached clip does indeed stop on its own — so right now the two paths
       agree, and this effect is the one that STATES the rule instead of
       inheriting it. That distinction stops being academic the moment the
       element is no longer unmounted: hide the inactive tab with CSS instead
       of unmounting it, or drop the per-clip `key` and reuse one element
       across clips, and nothing is ever detached — at which point this effect
       is the only thing that stops the sound. */
    const el = videoRef.current;
    if (el === null) return undefined;
    return () => { el.pause(); };
  }, [playingSourceId]);

  /**
   * A displayable URL for one Library row.
   *
   * The cover endpoint is unauthenticated and serves the stored thumbnail; the
   * file endpoint serves the original and needs the token. Preferring the
   * thumbnail keeps the panel cheap, and falling through to the original is a
   * real fallback rather than a placeholder — for an image resource the
   * original IS the picture.
   */
  const mediaUrl = (item: PreviewMedia): string =>
    item.thumbnail_url ?? getResourceFileUrl(item.id, mediaToken);

  /**
   * The playable URL for a clip. Always the file endpoint — a thumbnail is a
   * still, and this is the one place that needs the bytes.
   *
   * The token rides as `?token=` because a media element cannot send an
   * Authorization header. Seeking works because that endpoint answers Range
   * requests (`backend/app/services/library/media_serving.py` — FileResponse
   * for filesystem rows, an explicit 206 proxy for object-store ones), so
   * dragging the scrubber fetches the byte range rather than the whole file.
   */
  const videoUrl = (item: PreviewMedia): string => getResourceFileUrl(item.id, mediaToken);

  /** What the first grid cell shows, and why. Both halves together. */
  const coverSource: { url: string | null; provenance: CoverProvenance } = (() => {
    if (isImages) {
      // Not a fallback: an image post's cover IS its first image, which is
      // what the form says a few centimetres to the left.
      const first = items[0];
      return first
        ? { url: mediaUrl(first), provenance: 'firstImage' }
        : { url: null, provenance: 'none' };
    }
    const derived = covers?.[orientation];
    if (derived) {
      return { url: getResourceFileUrl(derived, mediaToken), provenance: 'cover' };
    }
    const first = items[0];
    if (first?.thumbnail_url) {
      return { url: first.thumbnail_url, provenance: 'thumbnail' };
    }
    return { url: null, provenance: 'none' };
  })();

  const provenanceNote = ((): string | null => {
    switch (coverSource.provenance) {
      case 'cover':
        return orientation === 'vertical'
          ? t('distribution.publish.previewCoverVertical', 'Showing your vertical 3:4 cover.')
          : t('distribution.publish.previewCoverHorizontal', 'Showing your horizontal 4:3 cover.');
      case 'firstImage':
        return t(
          'distribution.publish.previewCoverFirstImage',
          'Image posts take their cover from the first image.',
        );
      case 'thumbnail':
        return t(
          'distribution.publish.previewCoverThumbnail',
          'No cover set — this is the stored thumbnail, not a cover. The platform picks its own frame at publish time.',
        );
      case 'none':
      default:
        return null;
    }
  })();

  const titleText = title.trim();

  /** One image, or a stated reason there is none. Never a silent empty box. */
  const picture = (url: string | null, alt: string, missing: string) => {
    if (url === null) {
      return (
        <span className="pv-missing">
          <ImageOff size={14} aria-hidden="true" />
          {missing}
        </span>
      );
    }
    if (failedUrls.has(url)) {
      return (
        <span className="pv-fail" role="status">
          <AlertTriangle size={14} aria-hidden="true" />
          {t('distribution.publish.previewImageFailed', 'This image could not be loaded.')}
        </span>
      );
    }
    return <img src={url} alt={alt} onError={() => markFailed(url)} />;
  };

  /**
   * The prev / next / "n of total" strip, shared by the gallery and the video
   * tab because both page through the same selection. Sharing the index too:
   * the two tabs are mutually exclusive by post type, so they can never be
   * showing different positions at once.
   */
  const pager = (prevLabel: string, nextLabel: string) => (
    items.length === 0 ? null : (
      <div className="pv-pager">
        <button
          type="button"
          disabled={pageIndex === 0}
          aria-label={prevLabel}
          onClick={() => setGalleryIndex(pageIndex - 1)}
        >
          <ChevronLeft size={14} />
        </button>
        <span className="pv-count">
          {t('distribution.publish.previewPageCount', '{{n}} / {{total}}', {
            n: pageIndex + 1, total: items.length,
          })}
        </span>
        <button
          type="button"
          disabled={pageIndex >= items.length - 1}
          aria-label={nextLabel}
          onClick={() => setGalleryIndex(pageIndex + 1)}
        >
          <ChevronRight size={14} />
        </button>
      </div>
    )
  );

  const tabButton = (which: PreviewTab, label: string) => {
    const block = blockFor(which);
    const reason = block === null ? undefined
      : which === 'video'
        ? t('distribution.publish.previewTabVideoOnly', 'This is an image post — there is no video to play.')
        : t('distribution.publish.previewTabGalleryOnly', 'This is a video post — there is no image gallery.');
    return (
      <button
        type="button"
        role="tab"
        aria-selected={tab === which}
        disabled={block !== null}
        title={reason}
        onClick={() => setTab(which)}
      >
        {label}
      </button>
    );
  };

  const activeBlockReason = blockFor(tab) === null
    ? null
    : t(
      'distribution.publish.previewTabUnavailable',
      'This view does not apply to the selected post type.',
    );

  return (
    <div className="phone-card pv-card">
      <div className="bar">
        <h4>{t('distribution.publish.previewHeading', 'Preview')}</h4>
      </div>

      <div className="pv-tabs" role="tablist" aria-label={t('distribution.publish.previewHeading', 'Preview')}>
        {tabButton('video', t('distribution.publish.previewTabVideo', 'Video preview'))}
        {tabButton('gallery', t('distribution.publish.previewTabGallery', 'Gallery preview'))}
        {tabButton('cover', t('distribution.publish.previewTabCover', 'Cover & title'))}
      </div>

      <div className="phone">
        <span className="notch" />
        {activeBlockReason !== null ? (
          <div className="pv-screen pv-blocked" role="status">
            <AlertTriangle size={16} aria-hidden="true" />
            {activeBlockReason}
          </div>
        ) : tab === 'cover' ? (
          <div className="pv-screen">
            {/* Platform chrome. Drawn ONLY for the platform we have actually
                been shown — see DOUYIN_CHROME. Decoration end to end:
                aria-hidden, no buttons, no counts. It earns its place because
                it is the only thing that says "this is the feed", which is
                what makes the two-column card layout below legible as a feed
                rather than as a broken grid. */}
            {showsPlatformChrome && (
              <div className="pv-chrome-top" aria-hidden="true">
                {DOUYIN_CHROME.feedTabs.map((label) => (
                  <span key={label} className={label === DOUYIN_CHROME.currentTab ? 'cur' : ''}>
                    {label}
                  </span>
                ))}
              </div>
            )}

            <div className="pv-feed-scroll">
              <div
                className={`pv-feed ${orientation === 'vertical' ? 'v' : 'h'}`}
                /* `role="group"` so the label is announced — an `aria-label`
                   on a bare <div> has no role to attach to and is dropped by
                   most screen readers. It is the only thing telling a
                   non-sighted reader that every card but one is empty. */
                role="group"
                aria-label={t(
                  'distribution.publish.previewFeedAria',
                  'Feed preview — your post is one card; every card around it is an empty placeholder.',
                )}
              >
                {Array.from({ length: FEED_CARDS }, (_, i) => {
                  if (i !== MINE_INDEX) {
                    return <div className="pv-fcard empty" key={i} aria-hidden="true" />;
                  }
                  return (
                    <div className="pv-fcard mine" key={i}>
                      <div className="pv-fcover">
                        {picture(
                          coverSource.url,
                          t('distribution.publish.previewCoverAlt', 'Cover for this post'),
                          t('distribution.publish.previewNoCover', 'No cover yet'),
                        )}
                      </div>
                      <div className="pv-ffoot">
                        <span className={`pv-ftitle ${titleText === '' ? 'ph' : ''}`}>
                          {titleText === ''
                            ? t('distribution.publish.titlePlaceholderPreview', 'Your title appears here')
                            : titleText}
                        </span>
                        {/* The by-line only exists when there is a real account
                            behind it. No handle, no line — and no grey circle
                            standing in for an avatar the account does not have.
                            The heart is scenery and carries NO number: the post
                            has no likes, which is a different statement from
                            zero likes. */}
                        {handle !== null && (
                          <span className="pv-fby">
                            {avatarUrl !== null && (
                              <img className="pv-favatar" src={avatarUrl} alt="" />
                            )}
                            <span className="pv-fname">{handle}</span>
                            <Heart className="pv-fheart" size={9} aria-hidden="true" />
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* OURS, not the platform's. It sits where the screenshot puts it,
                which means it sits inside a mock-up of somebody else's app — so
                it is deliberately drawn in our accent on our own surface,
                carries our icon, and names itself in its accessible label. A
                control the user cannot tell apart from the platform's own is a
                control that teaches them something false about the platform. */}
            <div
              className="pv-ours"
              role="group"
              aria-label={t(
                'distribution.publish.previewOwnControlAria',
                'Preview control (part of this page, not the platform)',
              )}
            >
              <SlidersHorizontal size={10} aria-hidden="true" />
              <button
                type="button"
                className={orientation === 'vertical' ? 'on' : ''}
                onClick={() => onOrientationChange('vertical')}
              >
                {t('distribution.publish.vertical', 'Vertical')}
              </button>
              <button
                type="button"
                className={orientation === 'horizontal' ? 'on' : ''}
                onClick={() => onOrientationChange('horizontal')}
              >
                {t('distribution.publish.horizontal', 'Horizontal')}
              </button>
            </div>

            {showsPlatformChrome && (
              <div className="pv-chrome-bottom" aria-hidden="true">
                {DOUYIN_CHROME.navItems.map((label, i) => (
                  <span key={label} className={i === 2 ? 'plus' : ''}>{label}</span>
                ))}
              </div>
            )}
          </div>
        ) : tab === 'video' ? (
          <div className="pv-screen pv-gallery">
            <div className="pv-stage">
              {currentItem === null ? (
                <span className="pv-missing">
                  <ImageOff size={14} aria-hidden="true" />
                  {t('distribution.publish.previewNothingSelected', 'Nothing selected yet.')}
                </span>
              ) : failedUrls.has(videoUrl(currentItem)) ? (
                <span className="pv-fail" role="status">
                  <AlertTriangle size={14} aria-hidden="true" />
                  {t('distribution.publish.previewVideoFailed', 'This clip could not be loaded.')}
                </span>
              ) : (
                /* `key` gives every clip its own element, so switching clips
                   tears the previous decoder down instead of re-pointing a
                   live one. The explicit pause in the effect above is still
                   what guarantees the old element stops.

                   Native `controls` rather than a hand-rolled scrubber: it is
                   the seek bar the user already knows, it is keyboard- and
                   screen-reader-operable for free, and a custom one would be
                   drag behaviour that no test in this repo can actually
                   exercise (jsdom has no layout, so it has no pointer
                   geometry either).

                   No `autoPlay`: a preview that starts making noise the moment
                   a tab is clicked is worse than one click. */
                <video
                  key={currentItem.id}
                  ref={videoRef}
                  className="pv-video"
                  src={videoUrl(currentItem)}
                  poster={currentItem.thumbnail_url ?? undefined}
                  controls
                  preload="metadata"
                  aria-label={currentItem.filename}
                  onError={() => markFailed(videoUrl(currentItem))}
                />
              )}
            </div>
            <div className="pv-caption">
              {handle !== null && <div className="pv-handle">@{handle}</div>}
              <div className={`pv-cap ${titleText === '' ? 'ph' : ''}`}>
                {titleText === ''
                  ? t('distribution.publish.titlePlaceholderPreview', 'Your title appears here')
                  : titleText}
              </div>
            </div>
            {pager(
              t('distribution.publish.previewPrevClip', 'Previous clip'),
              t('distribution.publish.previewNextClip', 'Next clip'),
            )}
          </div>
        ) : (
          <div className="pv-screen pv-gallery">
            <div className="pv-stage">
              {currentItem === null
                ? (
                  <span className="pv-missing">
                    <ImageOff size={14} aria-hidden="true" />
                    {t('distribution.publish.previewNothingSelected', 'Nothing selected yet.')}
                  </span>
                )
                : picture(
                  mediaUrl(currentItem),
                  currentItem.filename,
                  t('distribution.publish.previewNoImage', 'No image to show'),
                )}
            </div>
            <div className="pv-caption">
              {handle !== null && <div className="pv-handle">@{handle}</div>}
              <div className={`pv-cap ${titleText === '' ? 'ph' : ''}`}>
                {titleText === ''
                  ? t('distribution.publish.titlePlaceholderPreview', 'Your title appears here')
                  : titleText}
              </div>
            </div>
            {pager(
              t('distribution.publish.previewPrevImage', 'Previous image'),
              t('distribution.publish.previewNextImage', 'Next image'),
            )}
          </div>
        )}
      </div>

      {tab === 'cover' && (
        <>
          {provenanceNote !== null && <p className="pv-note">{provenanceNote}</p>}
          <p className="pv-note">
            {t(
              'distribution.publish.previewFeedPlaceholderNote',
              'Every card except yours is an empty placeholder, not a real post.',
            )}
          </p>
          {showsPlatformChrome && (
            <p className="pv-note">
              {t(
                'distribution.publish.previewChromeNote',
                'The feed tabs and bottom bar are a sketch of the platform app. Only the highlighted switch belongs to this page.',
              )}
            </p>
          )}
        </>
      )}
    </div>
  );
};

export default PublishPreview;
