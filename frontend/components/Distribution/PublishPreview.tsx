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
 * ══ THE TWO SCREENS THIS PANEL REPRODUCES ══════════════════════════════════
 *
 * The cover view is the two-column recommendation FEED (above). The video and
 * gallery views are the IMMERSIVE full-screen player: the content fills the
 * whole screen and the app's interface floats on top of it.
 *
 * The immersive views were rebuilt because they were not reproducing anything.
 * They centred a small player inside an otherwise empty phone, with native
 * controls on it and the title printed underneath — which reads as "a video
 * embedded in a picture of a phone", not as "here is what this post looks
 * like". A preview whose whole value is that it is true was not true about the
 * one thing it is for.
 *
 * ⚠️ The two screens have DIFFERENT chrome, and they overlap enough to invite
 * the wrong copy: the feed's tab row is 精选/关注/推荐 and the player's is
 * 同城/关注/推荐. Two constants, named after their screen, neither a default for
 * the other — see `DOUYIN_FEATURED_FEED_CHROME` / `DOUYIN_IMMERSIVE_CHROME`.
 *
 * The fabrication rule above governs the floating layer without exception, and
 * that layer is where it is easiest to break: the rail carries glyphs and no
 * counts, the by-line and the avatar are drawn only when they are real, and no
 * decorative progress bar is drawn at all (it would assert a playback position
 * — a fabricated measurement is one whether it is a number or a bar).
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
  AlertTriangle, ChevronLeft, ChevronRight, Disc3, Heart, ImageOff, MessageCircle,
  Music2, Search, Share2, SlidersHorizontal, Star,
} from 'lucide-react';

import { getResourceFileUrl } from '../../services/resourceService';
import type { CoverPair } from './CoverSlots';

/** The three views the panel offers. */
export type PreviewTab = 'video' | 'gallery' | 'cover';

/** Which of the two derived crops the grid is drawn with. */
export type CoverOrientation = 'vertical' | 'horizontal';

/**
 * What, if anything, this page can truthfully say about the post's sound.
 *
 * A discriminated union rather than `string | null` because the three cases
 * are three different STATEMENTS, and only the caller knows which one holds:
 *
 *   `track`            a track was picked out of the platform's own catalogue,
 *                      so its real title (and author) are known facts.
 *   `platformDefault`  nothing was picked at all. `PublishPage` documents what
 *                      that means — the publish step leaves the platform's
 *                      music control alone, so the post keeps 原声. Known, not
 *                      guessed.
 *   `unresolved`       a search KEYWORD was typed but no track picked. The
 *                      browser will type that keyword into the platform's own
 *                      dialog and whatever comes back gets attached — five
 *                      character-identical titles under different ids is a
 *                      real case here (`music_ambiguous`). We do not know the
 *                      answer, so the line is not drawn.
 *
 * Collapsing this to a nullable string would push the decision into the view,
 * which does not have the information to make it — and the shape it would
 * reach for is exactly the unconditional "原声" this component's header names
 * as one of the fabrications it exists to prevent.
 */
export type PreviewSoundtrack =
  | { kind: 'track'; label: string }
  | { kind: 'platformDefault' }
  | { kind: 'unresolved' };

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
  /**
   * What the sound line may say — see `PreviewSoundtrack`.
   *
   * Required rather than optional on purpose: an omitted prop would default to
   * "say nothing", which is the silent version of the caller forgetting to
   * wire the music panel up at all. Making it explicit means the caller has to
   * state which of the three cases holds.
   */
  soundtrack: PreviewSoundtrack;
  /** Supabase JWT for `<img src>` / `<video src>` against the file endpoint,
   *  which takes it as `?token=` because a media element cannot send a header.
   *  Undefined while the session is still being read. */
  mediaToken?: string;
  orientation: CoverOrientation;
  onOrientationChange: (next: CoverOrientation) => void;
}

/**
 * Which views exist for each post type, in the order they are offered.
 *
 * ⚠️ THESE TABS ARE ABSENT, NOT DISABLED, AND THE DISTINCTION IS THE POINT.
 *
 * The house rule elsewhere is to disable a control and say why, because a
 * control the user cannot see is a control they cannot ask about. That rule
 * governs things that SHOULD apply here and happen not to right now — a
 * greyed-out button teaches "this exists, and here is what unlocks it".
 *
 * Neither of these is that. An image post has no video to play, ever, and it
 * has no cover to attach: the platform takes its cover FROM the first image
 * (`cover_not_supported_for_images` is a hard refusal on the backend, not a
 * capability flag that could flip). A greyed "Video preview" sitting over a
 * gallery does not teach the user anything true — it asks them to wonder what
 * would enable it, and the answer is nothing. A post is one type or the other:
 * `PublishPage` clears the whole selection when the type changes, so a batch
 * can never hold both — no reachable view is lost by not drawing them.
 */
const TABS_BY_KIND = {
  video: ['video', 'cover'],
  images: ['gallery'],
} as const satisfies Record<PublishPreviewProps['kind'], readonly PreviewTab[]>;

/**
 * Where the reader lands when the view they were on stops existing.
 *
 * ⚠️ Typed against the lists above rather than as a plain `PreviewTab`, so
 * naming a view this post type does not have is a COMPILE error. It would
 * otherwise be an infinite render: the correction below would replace the
 * missing tab with another missing tab, for ever.
 */
const DEFAULT_TAB: { [K in keyof typeof TABS_BY_KIND]: (typeof TABS_BY_KIND)[K][number] } = {
  video: 'cover',
  images: 'gallery',
};

/**
 * Where the image in the first grid cell came from.
 *
 * Carried explicitly rather than inferred at render time so the caption under
 * the grid cannot drift from the picture above it: a thumbnail shown because
 * no cover was derived means something different to the user ("the platform
 * will pick its own frame") than a cover they chose, and the difference has to
 * be stated, not left to be guessed from context.
 */
type CoverProvenance = 'cover' | 'thumbnail' | 'none';

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
/**
 * ⚠️ SCOPED TO ONE VIEW ON PURPOSE — do not reuse this for another tab.
 *
 * The tab row is not a property of the app, it is a property of the SCREEN.
 * The two-column card feed reproduced here shows 精选 / 关注 / 推荐 with 精选
 * selected; the full-screen video view shows a different set entirely
 * (同城 / 关注 / 推荐, with 推荐 selected). They share two words out of three,
 * which is exactly what makes copying one into the other easy and wrong.
 *
 * So the name says which screen it belongs to. If the video tab ever grows
 * chrome of its own, it gets its own constant — reusing this one would put a
 * real app's furniture in a room it does not stand in.
 *
 * ⚠️ The admission rule for BOTH chrome tables is written out on
 * `DOUYIN_IMMERSIVE_CHROME` below: every entry must be the literal text as it
 * was actually seen on the platform, and not-seen means not-drawn. Read it
 * before adding anything here.
 */
const DOUYIN_FEATURED_FEED_CHROME = {
  feedTabs: ['精选', '关注', '推荐'],
  currentTab: '精选',
  navItems: ['首页', '朋友', '＋', '消息', '我'],
} as const;

/**
 * ⚠️ THE OTHER SCREEN. Read the note on `DOUYIN_FEATURED_FEED_CHROME` first.
 *
 * This is the full-screen (immersive) player, not the two-column card feed,
 * and the two are DIFFERENT SCREENS of the same app:
 *
 *                       featured feed              immersive player
 *   tab row             精选 / 关注 / 推荐          同城 / 关注 / 推荐
 *   selected            精选                        推荐
 *
 * Two words out of three are shared, which is precisely what makes copying one
 * constant into the other easy to do and impossible to notice. They are kept
 * apart, named after their screen, and neither is a default for the other.
 *
 * Same fences as the featured-feed constant, without exception: verbatim, never
 * translated, `aria-hidden`, no hit targets, no counts, never mixed into our
 * own copy, and drawn ONLY when the post is actually going to Douyin.
 *
 * ⚠️ AND ONE ADMISSION RULE, which is what keeps every fence above honest:
 * every entry in this table must be the literal text as it was actually seen
 * on the platform. Not seen means NOT DRAWN — never a near-miss word picked
 * because the shape of the screen implies something belongs in that spot.
 * Printing a guessed word inside a reproduction of somebody else's app is the
 * same defect as an invented count, and a harder one to catch: a plausible
 * word reads as research. A gap here is the correct state of affairs until the
 * real text is in hand, so a missing badge is not an oversight to be filled in
 * from memory.
 *
 * `defaultSoundLabel` is the platform's own word for "no track attached" and
 * belongs to this table for the same reason the tab names do — it is a
 * depiction of what Douyin writes there, not a string we authored. It is only
 * ever reached through `soundtrack.kind === 'platformDefault'`; see
 * `PreviewSoundtrack` for why the caller, not this file, decides that.
 *
 * `imagePostMarker` is the badge Douyin puts beside the by-line on a gallery
 * post. Same category, one extra condition: it is a CONTENT-TYPE marker, so it
 * is drawn only for an image post, never on a video one — the platform does
 * not put it there, and a badge that appears on both marks nothing.
 */
const DOUYIN_IMMERSIVE_CHROME = {
  feedTabs: ['同城', '关注', '推荐'],
  currentTab: '推荐',
  navItems: ['首页', '朋友', '＋', '消息', '我'],
  defaultSoundLabel: '原声',
  imagePostMarker: '图文',
} as const;

export const PublishPreview: React.FC<PublishPreviewProps> = ({
  kind, items, title, handle, avatarUrl, platform, covers, soundtrack, mediaToken,
  orientation, onOrientationChange,
}) => {
  const { t } = useTranslation();

  const [tab, setTab] = useState<PreviewTab>(DEFAULT_TAB[kind]);
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
  /**
   * Whether the player's NATIVE control bar is currently drawn.
   *
   * ⚠️ This is the one knob where "looks right" and "can be tested" pull
   * against each other, so the trade is written down rather than left in a
   * commit message.
   *
   * The controls stay NATIVE — a hand-rolled scrubber is drag behaviour, and
   * jsdom has no layout, therefore no pointer geometry, therefore no test in
   * this repo could exercise it. Hand-rolling would move the most breakable
   * part of this panel outside the gate entirely.
   *
   * But a permanently-visible native bar (play button, volume slider, overflow
   * menu) is most of what made the old preview read as "a player embedded in a
   * picture of a phone" rather than as the post. So it is revealed on
   * INTERACTION instead of removed: pointer over the screen, a click/tap on
   * it, or keyboard focus on the player. Every one of those is a plain DOM
   * event, so the reveal rule itself IS testable — what stays untestable here
   * is only dragging the bar once it is on screen, exactly as before.
   *
   * `tabIndex={0}` on the player is load-bearing: a `<video>` WITHOUT
   * `controls` is not focusable, so without it the keyboard path could never
   * fire the focus that reveals the bar.
   */
  const [controlsVisible, setControlsVisible] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const markFailed = (url: string) => setFailedUrls((prev) => {
    if (prev.has(url)) return prev;
    const next = new Set(prev);
    next.add(url);
    return next;
  });

  const isImages = kind === 'images';
  /** See DOUYIN_FEATURED_FEED_CHROME: only the platform we have seen. */
  const showsPlatformChrome = platform === 'douyin';
  /**
   * The platform's 「图文」 badge — drawn only on a Douyin IMAGE post.
   *
   * Both halves are load-bearing. It is platform chrome, so it needs the same
   * gate as everything else in `DOUYIN_IMMERSIVE_CHROME`; and it is a
   * content-type marker, so putting it on a video post would mark a thing that
   * is not the case. A badge that shows up on both kinds distinguishes
   * nothing, which is worse than not drawing it.
   *
   * Not reachable from the cover view: `TABS_BY_KIND` offers that tab to video
   * posts only, so the two-column feed never renders an image post at all.
   */
  const showsImagePostMarker = showsPlatformChrome && isImages;

  const availableTabs: readonly PreviewTab[] = TABS_BY_KIND[kind];

  /**
   * ⚠️ CORRECTED DURING RENDER, NOT IN AN EFFECT, AND THAT IS DELIBERATE.
   *
   * Changing the post type can strand the reader on a view that no longer
   * exists — on an image post, `tab` may still read `'video'`. An effect would
   * fix that one commit too late, and the render in between is not harmless:
   * it would draw the video screen over a gallery selection, and the player's
   * stop rule reads the committed `tab`, so a frame of "no tab is selected and
   * a player is mounted for an image" is exactly the state this panel must
   * never be in. React re-runs the render instead of committing this one, so
   * the wrong frame never reaches the screen or the effects.
   *
   * Guarded by the same condition it fixes, so it settles in one extra pass.
   */
  if (!availableTabs.includes(tab)) setTab(DEFAULT_TAB[kind]);

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

  /** What the first grid cell shows, and why. Both halves together.
   *
   *  Only ever reached for a video post: `TABS_BY_KIND` does not offer the
   *  cover view to an image post, whose cover is not a separate asset at all. */
  const coverSource: { url: string | null; provenance: CoverProvenance } = (() => {
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

  /**
   * The text of the sound line, or null when this panel has nothing true to
   * put there. Read `PreviewSoundtrack` for who decides what.
   *
   * Two extra refusals live here rather than in the caller, because both are
   * facts about the SCREEN being drawn:
   *
   *  - No line at all off Douyin. The label would be the platform's own word,
   *    and putting it on a sketch of a different app is the same fabrication
   *    the chrome gate already stops.
   *  - No `platformDefault` line on an image post. "Leave the music control
   *    alone" means a video keeps its own recorded audio — 原声 is then a
   *    fact. A gallery has no recorded audio to keep, so what ends up on it is
   *    not something this page knows, and the honest line is no line.
   */
  const soundLabel = ((): string | null => {
    if (!showsPlatformChrome) return null;
    switch (soundtrack.kind) {
      case 'track': {
        const label = soundtrack.label.trim();
        return label === '' ? null : label;
      }
      case 'platformDefault':
        return isImages ? null : DOUYIN_IMMERSIVE_CHROME.defaultSoundLabel;
      case 'unresolved':
      default:
        return null;
    }
  })();

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
      /* OURS, floating inside a mock-up of somebody else's app — so it says so
         in its accessible name and is drawn in our accent, exactly like the
         orientation switch on the cover view. A control the reader cannot tell
         apart from the platform's own teaches them something false about the
         platform. */
      <div
        className="pv-pager"
        role="group"
        aria-label={t(
          'distribution.publish.previewOwnControlAria',
          'Preview control (part of this page, not the platform)',
        )}
      >
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

  /**
   * ══ THE IMMERSIVE VIEW'S FLOATING LAYER ═══════════════════════════════════
   *
   * Everything here is drawn ON TOP of content that fills the whole screen.
   * That is the entire shape of this view and the reason it was rebuilt: the
   * version before it centred a small player inside an otherwise empty phone
   * with the title printed underneath, which reads as "a video embedded in a
   * picture of a phone" rather than as the post. On the real screen the media
   * IS the background and the interface floats over it.
   *
   * Shared by the video and the gallery views because it is the same screen —
   * the only difference is whether a clip or a still is filling it.
   *
   * ⚠️ The rules from the top of this file apply here without exception, and
   * this layer is where they are easiest to break:
   *
   *   · The rail carries glyphs and NO numbers. Douyin puts a like / comment /
   *     favourite count beside each one; we do not have those numbers, and an
   *     unpublished post does not have them either. The icon says "this is a
   *     feed post"; a number claims a measurement.
   *   · The avatar is drawn only when the account really has one — no grey
   *     circle standing in for a picture that does not exist.
   *   · The by-line is drawn only when an account is actually selected. It
   *     used to read `@yourhandle`, a handle nobody owns, in the exact spot
   *     the real one goes.
   *   · No progress bar is drawn. Douyin has a thin one along the bottom, but
   *     a decorative one asserts a playback position, and the real position is
   *     already on the native control bar. A fabricated measurement is a
   *     fabricated measurement whether it is a number or a bar.
   */
  const immersiveOverlay = () => (
    <>
      {showsPlatformChrome && (
        <div className="pv-im-top" aria-hidden="true">
          {DOUYIN_IMMERSIVE_CHROME.feedTabs.map((label) => (
            <span key={label} className={label === DOUYIN_IMMERSIVE_CHROME.currentTab ? 'cur' : ''}>
              {label}
            </span>
          ))}
          <Search className="pv-im-search" size={11} />
        </div>
      )}

      {showsPlatformChrome && (
        <div className="pv-im-rail" aria-hidden="true">
          {avatarUrl !== null && <img className="pv-im-avatar" src={avatarUrl} alt="" />}
          <Heart size={15} />
          <MessageCircle size={15} />
          <Star size={15} />
          <Share2 size={15} />
          <Disc3 className="pv-im-disc" size={15} />
        </div>
      )}

      <div className="pv-caption">
        {/* The by-line row. Two independent things share it, and each is drawn
            on its own terms:

            · the handle, only when an account is really selected (it used to
              read `@yourhandle` — a handle nobody owns, in the exact position
              the real one goes);
            · the platform's gallery badge, only on an image post going to
              Douyin. It does NOT hang off the handle: which kind of post this
              is, is known whether or not an account has been picked yet.

            The badge sits BESIDE our content, never inside it — its own
            element, `aria-hidden`, never translated. Same fence as the tab row
            above: it is a depiction of the platform's screen, not our copy. */}
        {(handle !== null || showsImagePostMarker) && (
          <div className="pv-byline">
            {handle !== null && <span className="pv-handle">@{handle}</span>}
            {showsImagePostMarker && (
              <span className="pv-im-mark" aria-hidden="true">
                {DOUYIN_IMMERSIVE_CHROME.imagePostMarker}
              </span>
            )}
          </div>
        )}
        <div className={`pv-cap ${titleText === '' ? 'ph' : ''}`}>
          {titleText === ''
            ? t('distribution.publish.titlePlaceholderPreview', 'Your title appears here')
            : titleText}
        </div>
        {soundLabel !== null && (
          <div className="pv-sound">
            <Music2 size={9} aria-hidden="true" />
            <span>{soundLabel}</span>
          </div>
        )}
      </div>
    </>
  );

  /** The app's bottom navigation. Decoration, drawn last so it sits under the
   *  player's own control bar rather than over it. */
  const immersiveNav = () => (showsPlatformChrome ? (
    <div className="pv-im-nav" aria-hidden="true">
      {DOUYIN_IMMERSIVE_CHROME.navItems.map((label, i) => (
        <span key={label} className={i === 2 ? 'plus' : ''}>{label}</span>
      ))}
    </div>
  ) : null);

  const tabLabel = (which: PreviewTab): string => {
    switch (which) {
      case 'video':
        return t('distribution.publish.previewTabVideo', 'Video preview');
      case 'gallery':
        return t('distribution.publish.previewTabGallery', 'Gallery preview');
      case 'cover':
      default:
        return t('distribution.publish.previewTabCover', 'Cover & title');
    }
  };

  return (
    <div className="phone-card pv-card">
      <div className="bar">
        <h4>{t('distribution.publish.previewHeading', 'Preview')}</h4>
      </div>

      <div className="pv-tabs" role="tablist" aria-label={t('distribution.publish.previewHeading', 'Preview')}>
        {availableTabs.map((which) => (
          <button
            key={which}
            type="button"
            role="tab"
            aria-selected={tab === which}
            onClick={() => setTab(which)}
          >
            {tabLabel(which)}
          </button>
        ))}
      </div>

      <div className="phone">
        <span className="notch" />
        {tab === 'cover' ? (
          <div className="pv-screen">
            {/* Platform chrome. Drawn ONLY for the platform we have actually
                been shown — see DOUYIN_FEATURED_FEED_CHROME. Decoration end to end:
                aria-hidden, no buttons, no counts. It earns its place because
                it is the only thing that says "this is the feed", which is
                what makes the two-column card layout below legible as a feed
                rather than as a broken grid. */}
            {showsPlatformChrome && (
              <div className="pv-chrome-top" aria-hidden="true">
                {DOUYIN_FEATURED_FEED_CHROME.feedTabs.map((label) => (
                  <span key={label} className={label === DOUYIN_FEATURED_FEED_CHROME.currentTab ? 'cur' : ''}>
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
                {DOUYIN_FEATURED_FEED_CHROME.navItems.map((label, i) => (
                  <span key={label} className={i === 2 ? 'plus' : ''}>{label}</span>
                ))}
              </div>
            )}
          </div>
        ) : tab === 'video' ? (
          /* The clip fills the screen and the interface floats over it — see
             `immersiveOverlay`. The controls-on-interaction handlers live on
             this container rather than on the player so that pointing at any
             part of the screen counts, which is how the real thing behaves. */
          <div
            className={`pv-screen pv-gallery pv-im ${controlsVisible ? 'ctl' : ''}`}
            onMouseEnter={() => setControlsVisible(true)}
            onMouseLeave={() => setControlsVisible(false)}
            onClick={() => setControlsVisible(true)}
          >
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

                   `controls` is NATIVE but conditional — see `controlsVisible`
                   for the whole trade. `tabIndex` is what makes the keyboard
                   path reachable at all while the bar is hidden.

                   No `autoPlay`: a preview that starts making noise the moment
                   a tab is clicked is worse than one click. */
                <video
                  key={currentItem.id}
                  ref={videoRef}
                  className="pv-video"
                  src={videoUrl(currentItem)}
                  poster={currentItem.thumbnail_url ?? undefined}
                  controls={controlsVisible}
                  tabIndex={0}
                  preload="metadata"
                  aria-label={currentItem.filename}
                  onFocus={() => setControlsVisible(true)}
                  onBlur={() => setControlsVisible(false)}
                  onError={() => markFailed(videoUrl(currentItem))}
                />
              )}
            </div>
            {immersiveOverlay()}
            {pager(
              t('distribution.publish.previewPrevClip', 'Previous clip'),
              t('distribution.publish.previewNextClip', 'Next clip'),
            )}
            {immersiveNav()}
          </div>
        ) : (
          <div className="pv-screen pv-gallery pv-im">
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
            {immersiveOverlay()}
            {pager(
              t('distribution.publish.previewPrevImage', 'Previous image'),
              t('distribution.publish.previewNextImage', 'Next image'),
            )}
            {immersiveNav()}
          </div>
        )}
      </div>

      {tab !== 'cover' && showsPlatformChrome && (
        <p className="pv-note">
          {t(
            'distribution.publish.previewImmersiveChromeNote',
            'The tabs, side icons and bottom bar are a sketch of the platform app, with no counts because this post has none. Only the outlined pager belongs to this page.',
          )}
        </p>
      )}

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
