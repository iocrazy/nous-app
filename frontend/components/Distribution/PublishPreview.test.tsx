/**
 * The publish preview panel: the phone shell, its three tabs, the nine-cell
 * cover/title grid and the gallery pager.
 *
 * ⚠️ EVERY ASSERTION IN THIS FILE IS POSITIVE (`toBe('3 / 4')`, never
 * `not.toBe('4 / 3')`). The reason is specific, not stylistic: vitest runs
 * with `css: false`, so if the stylesheet ever stops being injected,
 * `getComputedStyle` returns `''` for every property — and `''` satisfies
 * every negative assertion ever written. A suite of `not.toBe(...)` would go
 * green on a page rendering with no styles at all, which is the exact shape of
 * a test that guards nothing. The same reasoning applies to the DOM
 * assertions: "the panel shows X" fails when the panel fails to render, while
 * "the panel does not show Y" passes when it renders nothing.
 *
 * Where absence really is the thing under test — the fabricated like counts
 * this panel exists to stop coming back — it is asserted as an EQUALITY on the
 * whole subtree's text, not as a `not.toContain`. An equality still fails when
 * the subtree is empty.
 *
 * ⚠️ TWO THINGS jsdom CANNOT DO, both of which shape what is asserted below.
 *
 * It does NOT resolve custom properties: any rule written as `var(--token)`
 * reads back as the property's default, whatever the token holds. So no
 * assertion here may depend on a token-derived colour — those are confirmed in
 * a real browser instead. Literal values (`aspect-ratio`, `grid-template-
 * columns`, `margin-top`) resolve normally and are asserted directly.
 *
 * And it does NOT do layout: every
 * `getBoundingClientRect()` is zeroes. So this file checks style RULES plus
 * the DOM shape they apply to. It cannot and does not measure that the grid
 * renders as three columns of the right proportion on screen — that was
 * checked in a real browser and recorded in the PR body, and is not
 * automated (CI runs vitest, not Playwright).
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import enJson from '../../public/locales/en.json';

vi.mock('../../services/resourceService', () => ({
  getResourceFileUrl: (id: string, token?: string) =>
    `/file/${id}${token === undefined ? '' : `?token=${token}`}`,
}));

import { PublishPreview, type PublishPreviewProps } from './PublishPreview';

/**
 * The real shipped stylesheet, read off disk.
 *
 * Not `import './distribution-v4.css'` and not Vite's `?raw`: under
 * `css: false` both hand back an empty string, every style assertion below
 * would read a browser default, and the file would pass while proving
 * nothing. A bad path throws here; the first test re-checks at run time that
 * the rules actually landed.
 */
const CSS = readFileSync(
  resolve(process.cwd(), 'components/Distribution/distribution-v4.css'),
  'utf8',
);

beforeAll(() => {
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);
});

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return inst;
};

const IMAGE_A = { id: 'img-a', filename: 'first.jpg', thumbnail_url: '/thumb/a' };
const IMAGE_B = { id: 'img-b', filename: 'second.jpg', thumbnail_url: '/thumb/b' };
const IMAGE_C = { id: 'img-c', filename: 'third.jpg', thumbnail_url: '/thumb/c' };
const CLIP = { id: 'clip-1', filename: 'take-4.mp4', thumbnail_url: '/thumb/clip' };

const baseProps: PublishPreviewProps = {
  kind: 'video',
  items: [CLIP],
  title: '',
  handle: null,
  avatarUrl: null,
  platform: 'douyin',
  covers: null,
  soundtrack: { kind: 'platformDefault' },
  mediaToken: undefined,
  orientation: 'vertical',
  onOrientationChange: () => {},
};

/** Render inside `.dist-v4`, which every rule in the stylesheet is scoped to. */
const renderPanel = (props: Partial<PublishPreviewProps> = {}) => {
  const merged = { ...baseProps, ...props };
  const Wrapper: React.FC<{ p: PublishPreviewProps }> = ({ p }) => (
    <I18nextProvider i18n={makeI18n()}>
      <div className="dist-v4">
        <PublishPreview {...p} />
      </div>
    </I18nextProvider>
  );
  const utils = render(<Wrapper p={merged} />);
  return {
    ...utils,
    /** Re-render with new props while keeping the same component instance. */
    update: (next: Partial<PublishPreviewProps>) =>
      utils.rerender(<Wrapper p={{ ...merged, ...next }} />),
  };
};

const feed = (): HTMLElement => screen.getByLabelText(
  'Feed preview — your post is one card; every card around it is an empty placeholder.',
);

/** The user's own card. Everything else in the feed is an empty placeholder. */
const mine = (): HTMLElement => feed().querySelector('.pv-fcard.mine') as HTMLElement;

const tab = (name: string): HTMLElement => screen.getByRole('tab', { name });

/**
 * Every tab on the row, in order, by its label.
 *
 * ⚠️ ASSERTED AS AN EQUALITY ON THE WHOLE LIST, never as "X is not there".
 * "The video tab is absent" is satisfied by a panel that failed to render at
 * all — it is the exact shape of a test that guards nothing. `toEqual([...])`
 * fails on an empty row as loudly as it fails on a row with one tab too many,
 * and `getAllByRole` throws outright when there is no row, so a broken panel
 * can never read as a passing "correctly hidden".
 */
const tabNames = (): string[] =>
  screen.getAllByRole('tab').map((el) => el.textContent ?? '');

/** The tabs currently marked selected — a list, so "none selected" and "two
 *  selected" both fail as clearly as "the wrong one selected". */
const selectedTabNames = (): string[] =>
  screen.getAllByRole('tab')
    .filter((el) => el.getAttribute('aria-selected') === 'true')
    .map((el) => el.textContent ?? '');

describe('the stylesheet under test is actually loaded', () => {
  it('resolves a rule that only this stylesheet defines', () => {
    renderPanel();
    // If the <style> injection above ever breaks, this reads '' and the whole
    // file's style assertions become vacuous — so it is checked directly.
    expect(getComputedStyle(screen.getByRole('tablist')).display).toBe('flex');
  });
});

describe('the two-column feed', () => {
  /**
   * ⚠️ This replaced a three-column nine-cell profile grid, which was the
   * wrong reference: the screen being reproduced is the recommendation FEED.
   * The column count is asserted POSITIVELY (`repeat(2, 1fr)`) — never as
   * "not three columns", which an unparsed stylesheet would satisfy by
   * returning ''.
   */
  it('lays the cards out two to a row', () => {
    renderPanel({ items: [CLIP], covers: { vertical: 'cv', horizontal: 'ch' } });
    expect(getComputedStyle(feed()).gridTemplateColumns).toBe('repeat(2, 1fr)');
  });

  it('fills the screen with cards, exactly one of which is the user’s', () => {
    renderPanel({ items: [CLIP], covers: { vertical: 'cv', horizontal: 'ch' } });
    const cards = feed().querySelectorAll('.pv-fcard');

    // Enough to overflow and be clipped at the bottom edge — a feed that stops
    // short of the screen reads as "there is nothing more", which is a claim.
    expect(cards.length).toBe(12);
    expect(feed().querySelectorAll('.pv-fcard.mine').length).toBe(1);
    expect(feed().querySelectorAll('.pv-fcard.empty').length).toBe(11);

    // No placeholder ever carries content.
    const others = Array.from(cards).filter((c) => !c.classList.contains('mine'));
    expect(others.reduce((n, c) => n + c.querySelectorAll('img').length, 0)).toBe(0);
    expect(others.filter((c) => c.getAttribute('aria-hidden') === 'true').length).toBe(11);
  });

  /**
   * Left column, first FULLY visible row — not the top-left corner, which is
   * where the discarded profile-grid version put it.
   *
   * Index 2 of a two-column grid is the left card of row 2, and the feed is
   * offset upwards so row 1 is cut off at the top. Both halves are asserted:
   * the DOM position, and the negative offset that does the cutting. jsdom has
   * no layout, so the offset is read as a style rule; the pixels were measured
   * in a real browser and are in the PR body.
   */
  it('puts the user’s card in the left column of the first fully visible row', () => {
    renderPanel({ items: [CLIP], covers: { vertical: 'cv', horizontal: 'ch' } });
    const cards = Array.from(feed().querySelectorAll('.pv-fcard'));
    expect(cards.indexOf(mine())).toBe(2);

    // The row above it is cut off — that is what makes row 2 the first fully
    // visible one rather than merely the second row.
    expect(getComputedStyle(feed()).marginTop).toBe('-76px');
    // Neutral values for the two properties that could reorder a grid child.
    expect(getComputedStyle(mine()).order).toBe('0');
    expect(getComputedStyle(feed()).direction).toBe('ltr');
  });

  it('says in words that the other cards are placeholders', () => {
    renderPanel();
    expect(
      screen.getByText('Every card except yours is an empty placeholder, not a real post.'),
    ).toBeTruthy();
  });

  /**
   * The regression guard for the defect this panel was rebuilt to remove: the
   * card it replaces rendered `0` beside a heart and `0` beside a speech
   * bubble, plus an unconditional "Original sound" line.
   *
   * Asserted as an EQUALITY on the card's entire text content rather than as a
   * `not.toContain('0')`: an equality fails both when a fabricated number
   * comes back AND when the card renders nothing at all.
   */
  it('shows the title and the handle, and no counts of any kind', () => {
    renderPanel({ title: 'Rooftop timelapse', items: [CLIP], handle: 'realaccount' });
    // The heart is scenery; it is aria-hidden and carries no number, so the
    // card's text is exactly the title followed by the handle.
    expect(mine().textContent).toBe('Rooftop timelapserealaccount');
    expect(mine().querySelectorAll('.pv-fheart').length).toBe(1);
  });

  it('draws no by-line at all when no account is selected', () => {
    renderPanel({ title: 'Rooftop timelapse', items: [CLIP], handle: null });
    // Equality again: an invented `@yourhandle` returning turns this red.
    expect(mine().textContent).toBe('Rooftop timelapse');
    expect(mine().querySelectorAll('.pv-fby').length).toBe(0);
  });

  it('shows a real avatar, and draws none when the account has no picture', () => {
    const { update } = renderPanel({ items: [CLIP], handle: 'realaccount', avatarUrl: null });
    // No grey circle standing in for a picture the account does not have.
    expect(mine().querySelectorAll('.pv-favatar').length).toBe(0);

    update({ avatarUrl: '/avatar/real.png' });
    expect(mine().querySelector('.pv-favatar')?.getAttribute('src')).toBe('/avatar/real.png');
  });

  it('marks an empty title as a placeholder instead of leaving a blank strip', () => {
    renderPanel({ title: '' });
    const caption = mine().querySelector('.pv-ftitle');
    expect(caption?.textContent).toBe('Your title appears here');
    expect(caption?.classList.contains('ph')).toBe(true);
  });
});

/**
 * The borrowed interface around the cards.
 *
 * It is drawn only for the one platform we have actually been shown. Drawing
 * Douyin's feed around a Xiaohongshu post would be inventing a different app's
 * screen — the same defect as a fabricated like count, one level up.
 */
describe('the platform chrome', () => {
  it('draws the feed tabs and bottom bar for the platform it depicts', () => {
    const { container } = renderPanel({ platform: 'douyin', items: [CLIP] });
    expect(container.querySelectorAll('.pv-chrome-top').length).toBe(1);
    expect(container.querySelectorAll('.pv-chrome-bottom').length).toBe(1);
  });

  it('draws none of it for a platform whose feed we have not seen', () => {
    const { container } = renderPanel({ platform: 'xiaohongshu', items: [CLIP] });
    expect(container.querySelectorAll('.pv-chrome-top').length).toBe(0);
    expect(container.querySelectorAll('.pv-chrome-bottom').length).toBe(0);
    // ...and the cards are still there, so this is a chrome decision rather
    // than the whole view failing to render.
    expect(feed().querySelectorAll('.pv-fcard.mine').length).toBe(1);
  });

  it('draws none of it before an account has been chosen', () => {
    const { container } = renderPanel({ platform: null, items: [CLIP] });
    expect(container.querySelectorAll('.pv-chrome-top').length).toBe(0);
    expect(container.querySelectorAll('.pv-chrome-bottom').length).toBe(0);
  });

  it('is scenery: hidden from assistive tech, and not clickable', () => {
    const { container } = renderPanel({ platform: 'douyin', items: [CLIP] });
    const top = container.querySelector('.pv-chrome-top') as HTMLElement;
    const bottom = container.querySelector('.pv-chrome-bottom') as HTMLElement;
    expect(top.getAttribute('aria-hidden')).toBe('true');
    expect(bottom.getAttribute('aria-hidden')).toBe('true');
    // No hit targets anywhere in it — a decoration that looks operable is a
    // promise the preview cannot keep.
    expect(top.querySelectorAll('button, a, input').length).toBe(0);
    expect(bottom.querySelectorAll('button, a, input').length).toBe(0);
  });

  it('says which parts of the phone are borrowed and which are ours', () => {
    renderPanel({ platform: 'douyin', items: [CLIP] });
    expect(screen.getByText(
      'The feed tabs and bottom bar are a sketch of the platform app. Only the highlighted switch belongs to this page.',
    )).toBeTruthy();
  });
});

describe('the orientation switch is recognisably ours', () => {
  /**
   * ⚠️ The colour half of "recognisably ours" is NOT asserted here, and that is
   * a limitation rather than an oversight.
   *
   * The pill is drawn with `var(--t-indigo)` on `var(--card)`, and **jsdom
   * does not resolve custom properties at all** — `getComputedStyle` hands
   * back `rgb(0, 0, 0)` / `rgba(0, 0, 0, 0)` for any `var()` rule no matter
   * what the token is set to (probed directly before writing this). So a
   * colour assertion here could only ever be a statement about jsdom's
   * defaults, and a `not.toBe(...)` version would pass vacuously — the exact
   * shape this file exists to avoid. The accent is confirmed by eye in a real
   * browser; see the PR body.
   *
   * What IS asserted is the structural half, which is the part that actually
   * separates our control from the scenery around it: ours is operable and
   * carries a name that says whose it is; the platform's chrome is inert and
   * hidden from assistive tech.
   */
  it('lives inside the phone but names itself as this page’s control', () => {
    const { container } = renderPanel({ platform: 'douyin', items: [CLIP] });
    const own = screen.getByRole('group', {
      name: 'Preview control (part of this page, not the platform)',
    });

    // Inside the phone, where the reference puts it.
    expect(container.querySelectorAll('.phone .pv-ours').length).toBe(1);
    // Operable, unlike every borrowed pixel around it.
    expect(own.querySelectorAll('button').length).toBe(2);
    // And not swallowed by the chrome's aria-hidden: a control the screen
    // reader cannot reach is a control that is ours in name only.
    expect(own.closest('[aria-hidden="true"]')).toBe(null);
  });

  it('offers it whichever platform is selected, chrome or no chrome', () => {
    renderPanel({ platform: 'xiaohongshu', items: [CLIP] });
    expect(screen.getByRole('button', { name: 'Vertical' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Horizontal' })).toBeEnabled();
  });
});

describe('the vertical / horizontal switch', () => {
  it('draws 3:4 covers for vertical and 4:3 covers for horizontal', () => {
    const cover = () => mine().querySelector('.pv-fcover') as Element;

    const { update } = renderPanel({ orientation: 'vertical' });
    expect(getComputedStyle(cover()).aspectRatio).toBe('3 / 4');
    // The empty cards follow the same shape, so the columns stay on one
    // rhythm rather than the user's card standing out by size alone.
    expect(getComputedStyle(feed().querySelector('.pv-fcard.empty') as Element).aspectRatio)
      .toBe('3 / 4.62');

    update({ orientation: 'horizontal' });
    expect(getComputedStyle(cover()).aspectRatio).toBe('4 / 3');
    expect(getComputedStyle(feed().querySelector('.pv-fcard.empty') as Element).aspectRatio)
      .toBe('4 / 3.9');
  });

  /* Shorter cards need a smaller bite taken out of the top row, or the cut-off
     row stops being a cut-off row. */
  it('keeps the top row cut off in both orientations', () => {
    const { update } = renderPanel({ orientation: 'vertical' });
    expect(getComputedStyle(feed()).marginTop).toBe('-76px');

    update({ orientation: 'horizontal' });
    expect(getComputedStyle(feed()).marginTop).toBe('-52px');
  });

  it('shows the matching cover crop, not just a differently shaped box', () => {
    const { update } = renderPanel({
      orientation: 'vertical',
      covers: { vertical: 'cover-v', horizontal: 'cover-h' },
    });
    expect(mine().querySelector('img')?.getAttribute('src')).toBe('/file/cover-v');

    update({ orientation: 'horizontal' });
    expect(mine().querySelector('img')?.getAttribute('src')).toBe('/file/cover-h');
  });

  it('reports the pressed state back to the page', () => {
    const onOrientationChange = vi.fn();
    renderPanel({ onOrientationChange });
    fireEvent.click(screen.getByRole('button', { name: 'Horizontal' }));
    expect(onOrientationChange).toHaveBeenCalledWith('horizontal');
  });
});

describe('where the first cell’s picture comes from is stated, never implied', () => {
  it('names the crop when the user has derived covers', () => {
    renderPanel({ covers: { vertical: 'cover-v', horizontal: 'cover-h' } });
    expect(screen.getByText('Showing your vertical 3:4 cover.')).toBeTruthy();
  });

  /* The caption names a specific crop, so it has to follow the crop actually
     on screen. A caption stuck on "vertical" while the horizontal cover is
     displayed is a sentence about the picture that is not true of the picture
     — the same defect as a fabricated count, in prose. */
  it('renames the crop when the switch is flipped', () => {
    const { update } = renderPanel({
      covers: { vertical: 'cover-v', horizontal: 'cover-h' },
      orientation: 'vertical',
    });
    expect(screen.getByText('Showing your vertical 3:4 cover.')).toBeTruthy();

    update({ orientation: 'horizontal' });
    expect(screen.getByText('Showing your horizontal 4:3 cover.')).toBeTruthy();
    expect(mine().querySelector('img')?.getAttribute('src')).toBe('/file/cover-h');
  });

  it('falls back to the thumbnail AND says it is not a cover', () => {
    renderPanel({ covers: null, items: [CLIP] });
    expect(mine().querySelector('img')?.getAttribute('src')).toBe('/thumb/clip');
    expect(screen.getByText(
      'No cover set — this is the stored thumbnail, not a cover. The platform picks its own frame at publish time.',
    )).toBeTruthy();
  });

  it('states the absence rather than drawing an empty box', () => {
    renderPanel({ covers: null, items: [] });
    expect(within(mine()).getByText('No cover yet')).toBeTruthy();
  });

  it('signs the file URL with the session token when there is one', () => {
    renderPanel({ covers: { vertical: 'cover-v', horizontal: 'cover-h' }, mediaToken: 'jwt' });
    expect(mine().querySelector('img')?.getAttribute('src')).toBe('/file/cover-v?token=jwt');
  });
});

describe('a picture that fails to load says so', () => {
  it('replaces the cell with a stated failure instead of an empty box', () => {
    renderPanel({ covers: { vertical: 'cover-v', horizontal: 'cover-h' } });
    fireEvent.error(mine().querySelector('img') as Element);
    expect(within(mine()).getByText('This image could not be loaded.')).toBeTruthy();
  });

  it('re-tries when the URL changes, rather than staying failed forever', () => {
    const { update } = renderPanel({ covers: { vertical: 'cover-v', horizontal: 'cover-h' } });
    fireEvent.error(mine().querySelector('img') as Element);
    expect(within(mine()).getByText('This image could not be loaded.')).toBeTruthy();

    // A token arriving late produces a different URL — that attempt deserves
    // its own verdict.
    update({ mediaToken: 'jwt' });
    expect(mine().querySelector('img')?.getAttribute('src')).toBe('/file/cover-v?token=jwt');
  });
});

/**
 * The row carries the views this post type HAS, and nothing else.
 *
 * These tabs used to be drawn for both types and greyed out for the one they
 * did not apply to. The house rule that produced that — disable and say why,
 * rather than hide — is about controls that SHOULD apply and happen not to
 * yet; a greyed control teaches "this exists, here is what unlocks it". An
 * image post has no video to play and no cover to attach, ever, so the greyed
 * pair taught nothing and asked the reader to wonder what would enable them.
 */
describe('the tab row offers exactly the views the post type has', () => {
  it('gives a video post the player and the cover view, opening on the cover', () => {
    renderPanel({ kind: 'video', items: [CLIP] });
    expect(tabNames()).toEqual(['Video preview', 'Cover & title']);
    expect(selectedTabNames()).toEqual(['Cover & title']);
  });

  it('gives an image post the gallery and nothing else, opening on it', () => {
    renderPanel({ kind: 'images', items: [IMAGE_A] });
    expect(tabNames()).toEqual(['Gallery preview']);
    expect(selectedTabNames()).toEqual(['Gallery preview']);
  });

  it('swaps the row in both directions as the post type changes', () => {
    const { update } = renderPanel({ kind: 'video', items: [CLIP] });
    expect(tabNames()).toEqual(['Video preview', 'Cover & title']);

    update({ kind: 'images', items: [IMAGE_A] });
    expect(tabNames()).toEqual(['Gallery preview']);

    update({ kind: 'video', items: [CLIP] });
    expect(tabNames()).toEqual(['Video preview', 'Cover & title']);
  });

  /**
   * ⚠️ THE STEP THAT IS EASIEST TO FORGET.
   *
   * Removing a tab the reader is standing on is how a panel ends up with no
   * tab selected and a blank screen under it. Both halves are asserted: the
   * row's one remaining tab is the selected one, AND the screen below shows
   * the content that view is for.
   */
  it('lands the reader on a real view when the one they were on is removed', () => {
    const { update } = renderPanel({ kind: 'video', items: [CLIP] });
    fireEvent.click(tab('Video preview'));
    expect(selectedTabNames()).toEqual(['Video preview']);

    update({ kind: 'images', items: [IMAGE_A] });
    expect(tabNames()).toEqual(['Gallery preview']);
    expect(selectedTabNames()).toEqual(['Gallery preview']);
    expect(document.querySelector('.pv-stage img')?.getAttribute('src')).toBe('/thumb/a');
  });

  it('lands on the cover view when an image post becomes a video post', () => {
    const { update } = renderPanel({ kind: 'images', items: [IMAGE_A] });
    expect(selectedTabNames()).toEqual(['Gallery preview']);

    update({ kind: 'video', items: [CLIP] });
    expect(tabNames()).toEqual(['Video preview', 'Cover & title']);
    expect(selectedTabNames()).toEqual(['Cover & title']);
    expect(mine().querySelector('img')?.getAttribute('src')).toBe('/thumb/clip');
  });
});

describe('the gallery pager', () => {
  const openGallery = (items = [IMAGE_A, IMAGE_B, IMAGE_C]) => {
    const utils = renderPanel({ kind: 'images', items });
    fireEvent.click(tab('Gallery preview'));
    return utils;
  };

  it('walks the selected images in order', () => {
    openGallery();
    expect(screen.getByText('1 / 3')).toBeTruthy();
    expect(document.querySelector('.pv-stage img')?.getAttribute('src')).toBe('/thumb/a');

    fireEvent.click(screen.getByRole('button', { name: 'Next image' }));
    expect(screen.getByText('2 / 3')).toBeTruthy();
    expect(document.querySelector('.pv-stage img')?.getAttribute('src')).toBe('/thumb/b');

    fireEvent.click(screen.getByRole('button', { name: 'Previous image' }));
    expect(screen.getByText('1 / 3')).toBeTruthy();
    expect(document.querySelector('.pv-stage img')?.getAttribute('src')).toBe('/thumb/a');
  });

  it('disables the ends rather than letting them run off', () => {
    openGallery();
    expect(screen.getByRole('button', { name: 'Previous image' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Next image' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Next image' }));
    fireEvent.click(screen.getByRole('button', { name: 'Next image' }));
    expect(screen.getByText('3 / 3')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Next image' })).toBeDisabled();
  });

  /**
   * ⚠️ THE ONE THIS FILE EXISTS FOR.
   *
   * The parent rebuilds the selected-media array on every render that touches
   * the Library list, so an array-identity dependency would throw the reader
   * back to the first image for reasons the reader did not cause. The page has
   * already shipped that bug once, on the music panel, where a refresh that
   * changed nothing on screen cut off the track being auditioned.
   *
   * A NEW array holding the SAME ids must leave the page number alone.
   */
  it('keeps your page when the selection array is rebuilt with the same images', () => {
    const { update } = openGallery();
    fireEvent.click(screen.getByRole('button', { name: 'Next image' }));
    expect(screen.getByText('2 / 3')).toBeTruthy();

    // Same ids, brand-new objects and a brand-new array — exactly what a
    // Library refresh produces.
    update({
      items: [
        { ...IMAGE_A }, { ...IMAGE_B }, { ...IMAGE_C },
      ],
    });
    expect(screen.getByText('2 / 3')).toBeTruthy();
  });

  it('goes back to the first image when the selection really changes', () => {
    const { update } = openGallery();
    fireEvent.click(screen.getByRole('button', { name: 'Next image' }));
    expect(screen.getByText('2 / 3')).toBeTruthy();

    update({ items: [IMAGE_C, IMAGE_A] });
    expect(screen.getByText('1 / 2')).toBeTruthy();
    expect(document.querySelector('.pv-stage img')?.getAttribute('src')).toBe('/thumb/c');
  });

  it('nests the gallery the way the CSS rules assume', () => {
    const { container } = renderPanel({ kind: 'images', items: [IMAGE_A] });
    fireEvent.click(tab('Gallery preview'));
    expect(container.querySelectorAll('.phone > .pv-screen.pv-gallery').length).toBe(1);
    expect(container.querySelectorAll('.pv-gallery > .pv-stage > img').length).toBe(1);
    expect(container.querySelectorAll('.pv-gallery > .pv-caption').length).toBe(1);
    expect(container.querySelectorAll('.pv-gallery > .pv-pager').length).toBe(1);
  });

  it('states an empty selection instead of showing a blank screen', () => {
    renderPanel({ kind: 'images', items: [] });
    fireEvent.click(tab('Gallery preview'));
    expect(screen.getByText('Nothing selected yet.')).toBeTruthy();
  });

  it('shows the caption with the real handle, and only the title without one', () => {
    const { update } = openGallery();
    const caption = () => document.querySelector('.pv-caption') as HTMLElement;
    // No account picked: the caption is the title alone. Asserted as an
    // equality so an invented `@yourhandle` coming back turns this red.
    expect(caption().textContent).toBe('Your title appears here');

    update({ title: 'Studio tour', handle: 'realaccount' });
    expect(caption().textContent).toBe('@realaccountStudio tour');
  });
});

describe('the video tab', () => {
  const CLIP_B = { id: 'clip-2', filename: 'take-5.mp4', thumbnail_url: '/thumb/clip-b' };
  const CLIP_C = { id: 'clip-3', filename: 'take-6.mp4', thumbnail_url: null };

  const openVideo = (props: Partial<PublishPreviewProps> = {}) => {
    const utils = renderPanel({ kind: 'video', items: [CLIP], ...props });
    fireEvent.click(tab('Video preview'));
    return utils;
  };

  const player = (): HTMLVideoElement =>
    document.querySelector('.pv-stage video') as HTMLVideoElement;

  it('plays the selected clip from the file endpoint, signed with the session token', () => {
    openVideo({ mediaToken: 'jwt' });
    expect(player().getAttribute('src')).toBe('/file/clip-1?token=jwt');
  });

  it('keeps the seek bar native and reachable, and does not start on its own', () => {
    openVideo();
    const el = player();
    /* Native controls ARE the draggable progress bar. jsdom cannot drag one —
       no layout means no pointer geometry — so what is pinned here is that the
       control surface is the native one and that playback is not automatic.
       Actual dragging was exercised in a real browser; see the PR body.

       `tabIndex` is asserted because it is load-bearing rather than cosmetic:
       a <video> WITHOUT `controls` is not focusable, so dropping it would take
       the keyboard route to the (now hidden) bar away entirely. */
    expect(el.tagName).toBe('VIDEO');
    expect(el.getAttribute('tabindex')).toBe('0');
    expect(el.getAttribute('preload')).toBe('metadata');
    expect(el.autoplay).toBe(false);
  });

  it('uses the clip’s own thumbnail as the poster frame when there is one', () => {
    const { update } = openVideo({ items: [CLIP, CLIP_C] });
    expect(player().getAttribute('poster')).toBe('/thumb/clip');

    // No thumbnail is not a reason to invent one: the attribute is simply
    // absent, and the element falls back to its own first frame.
    update({ items: [CLIP_C] });
    expect(player().getAttribute('poster')).toBe(null);
  });

  it('says so when the clip cannot be loaded', () => {
    openVideo();
    fireEvent.error(player());
    expect(screen.getByText('This clip could not be loaded.')).toBeTruthy();
  });

  it('pages through several selected clips', () => {
    openVideo({ items: [CLIP, CLIP_B, CLIP_C] });
    expect(screen.getByText('1 / 3')).toBeTruthy();
    expect(player().getAttribute('src')).toBe('/file/clip-1');
    expect(screen.getByRole('button', { name: 'Previous clip' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Next clip' }));
    expect(screen.getByText('2 / 3')).toBeTruthy();
    expect(player().getAttribute('src')).toBe('/file/clip-2');
  });

  it('states an empty selection instead of mounting a player with no source', () => {
    renderPanel({ kind: 'video', items: [] });
    fireEvent.click(tab('Video preview'));
    expect(screen.getByText('Nothing selected yet.')).toBeTruthy();
    expect(document.querySelectorAll('.pv-stage video').length).toBe(0);
  });
});

/**
 * ══ WHEN THE PLAYER STOPS ═══════════════════════════════════════════════════
 *
 * Sound whose source the user can no longer see is sound the user cannot stop.
 * The cases below are the whole contract — and the last one, the case that
 * must NOT stop it, is the reason the rule is written the way it is.
 *
 * `pause` is spied on the prototype because jsdom's own implementation is a
 * stub; the spy is both the stand-in and the assertion surface.
 */
describe('playback stops when the user loses sight of it', () => {
  const CLIP_B = { id: 'clip-2', filename: 'take-5.mp4', thumbnail_url: null };

  let pauseSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    pauseSpy = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  });
  afterEach(() => { pauseSpy.mockRestore(); });

  const openVideo = (props: Partial<PublishPreviewProps> = {}) => {
    const utils = renderPanel({ kind: 'video', items: [CLIP], ...props });
    fireEvent.click(tab('Video preview'));
    return utils;
  };

  it('stops when the reader switches to another tab', () => {
    openVideo();
    expect(pauseSpy.mock.calls.length).toBe(0);

    fireEvent.click(tab('Cover & title'));
    expect(pauseSpy.mock.calls.length).toBe(1);
  });

  /**
   * ⚠️ WHICH element gets paused is the whole substance of this one.
   *
   * Every clip gets its own element (`key`), so on a clip change React has
   * already detached the old one and pointed the ref at the new one by the
   * time the effect cleanup runs. A cleanup that reads `videoRef.current`
   * therefore pauses the NEW element — `pause` is still called exactly once,
   * the call count still reads 1, and the OLD element goes on playing to an
   * empty room. Counting calls cannot tell those two apart; `mock.contexts`
   * can, so the identity is asserted rather than the count alone.
   */
  it('stops the clip being left behind, not the one being opened', () => {
    openVideo({ items: [CLIP, CLIP_B] });
    const leaving = document.querySelector('.pv-stage video');
    expect(pauseSpy.mock.calls.length).toBe(0);

    fireEvent.click(screen.getByRole('button', { name: 'Next clip' }));
    const opened = document.querySelector('.pv-stage video');

    expect(pauseSpy.mock.calls.length).toBe(1);
    expect(pauseSpy.mock.contexts[0]).toBe(leaving);
    // And the new element really is a different one, so the check above is a
    // distinction and not a tautology.
    expect(opened === leaving).toBe(false);
  });

  it('stops when the panel unmounts', () => {
    const { unmount } = openVideo();
    expect(pauseSpy.mock.calls.length).toBe(0);

    unmount();
    expect(pauseSpy.mock.calls.length).toBe(1);
  });

  it('stops when the selection is emptied out from under it', () => {
    const { update } = openVideo();
    expect(pauseSpy.mock.calls.length).toBe(0);

    update({ items: [] });
    expect(pauseSpy.mock.calls.length).toBe(1);
  });

  /**
   * ⚠️ THE CASE THE HIDDEN VIDEO TAB CREATES.
   *
   * An image post has no video view at all, so the tab the player lived on
   * disappears out from under the reader. Hiding a player is not stopping it:
   * a `<video>` kept alive behind `display:none` goes on making noise from a
   * control that is no longer on screen, which is the worst version of this
   * defect because there is then nothing left to press.
   *
   * So three things are checked, not one: the element that was playing is the
   * one that got paused, it is gone from the tree rather than merely hidden,
   * and the gallery really did render in its place — without that last line a
   * panel that rendered nothing at all would satisfy the middle one.
   */
  it('stops when the post turns into an image post, and the player is gone', () => {
    const { update } = openVideo();
    const playing = document.querySelector('.pv-stage video');
    expect(pauseSpy.mock.calls.length).toBe(0);

    update({ kind: 'images', items: [IMAGE_A] });
    expect(pauseSpy.mock.calls.length).toBe(1);
    expect(pauseSpy.mock.contexts[0]).toBe(playing);
    expect(document.querySelectorAll('.pv-stage video').length).toBe(0);
    expect(document.querySelector('.pv-stage img')?.getAttribute('src')).toBe('/thumb/a');
  });

  /**
   * ⚠️ THE ONE THAT MUST NOT STOP IT.
   *
   * The page shipped this bug on the music panel: the audition's stop rule
   * hung off the search results array's IDENTITY, the search effect rebuilt
   * that array for reasons the user did not cause, and so a refresh that
   * changed nothing whatsoever on screen cut off the track being listened to.
   *
   * Both halves matter. The first proves a no-op rebuild leaves playback
   * alone; the second proves the spy would have caught it — without that, a
   * `pause` that never fires at all would satisfy the first half vacuously.
   */
  it('does NOT stop when the selection array is rebuilt with the same clip', () => {
    const { update } = openVideo({ items: [CLIP, CLIP_B] });
    const before = document.querySelector('.pv-stage video');

    // Same ids, brand-new objects, brand-new array — a Library refresh.
    update({ items: [{ ...CLIP }, { ...CLIP_B }] });
    expect(pauseSpy.mock.calls.length).toBe(0);
    // Same element too: the decoder was never torn down and rebuilt.
    expect(document.querySelector('.pv-stage video')).toBe(before);

    // The control: a real change to the clip being played DOES stop it, so the
    // zero above is a fact about the rule and not about a dead spy.
    fireEvent.click(screen.getByRole('button', { name: 'Next clip' }));
    expect(pauseSpy.mock.calls.length).toBe(1);
  });
});

/**
 * ══ THE IMMERSIVE (FULL-SCREEN) VIEW ════════════════════════════════════════
 *
 * The video and gallery views draw the content edge to edge with the app's
 * interface floating over it. What they replaced centred a small player in an
 * otherwise empty phone with the title printed underneath, which reads as a
 * video embedded in a picture of a phone rather than as the post.
 *
 * ⚠️ WHAT THIS FILE CAN AND CANNOT SAY ABOUT "FILLS THE SCREEN".
 *
 * It cannot say it. jsdom has no layout engine, so every
 * `getBoundingClientRect()` is zeroes and no assertion here can measure that
 * anything covers anything. What IS asserted is the style RULES that produce
 * it (`position`, `width`, `height`, `object-fit`, `z-index` — all literal
 * values, which jsdom does resolve) plus the DOM the rules apply to. The
 * geometry itself was measured in a real browser and written into the PR body;
 * dressing a rule assertion up as a layout assertion would be the more
 * dangerous of the two possible mistakes here.
 *
 * ⚠️ AND THE FALSE GREEN THIS SECTION IS MOST EXPOSED TO.
 *
 * The headline defect being guarded against is invented interface data — a
 * like count, a fabricated sound line, a handle nobody owns. "The screen shows
 * no numbers" is satisfied just as well by a screen that failed to render at
 * all. So absence is never asserted on its own: the screen's whole text is
 * pinned as an EQUALITY against the exact string it should have, which fails
 * both when something is invented AND when the overlay stops rendering.
 */
describe('the immersive full-screen view', () => {
  const CLIP_2 = { id: 'clip-2', filename: 'take-5.mp4', thumbnail_url: null };

  const openVideo = (props: Partial<PublishPreviewProps> = {}) => {
    const utils = renderPanel({ kind: 'video', items: [CLIP], ...props });
    fireEvent.click(tab('Video preview'));
    return utils;
  };
  const openGallery = (props: Partial<PublishPreviewProps> = {}) => {
    const utils = renderPanel({ kind: 'images', items: [IMAGE_A], ...props });
    fireEvent.click(tab('Gallery preview'));
    return utils;
  };

  const immersive = (): HTMLElement =>
    document.querySelector('.pv-screen.pv-im') as HTMLElement;
  const stage = (): HTMLElement =>
    document.querySelector('.pv-im .pv-stage') as HTMLElement;

  /**
   * The content is the background, not an object sitting on one.
   *
   * Both views are checked because they took the fix separately, and a gallery
   * left on the old centred rules would look exactly as wrong as the player
   * did — this is the pair that would have gone green on half a fix.
   */
  it('lays the clip out as the screen itself rather than as a box on it', () => {
    openVideo();
    expect(window.getComputedStyle(stage()).position).toBe('absolute');

    const media = window.getComputedStyle(
      document.querySelector('.pv-im .pv-stage .pv-video') as HTMLElement,
    );
    expect(media.width).toBe('100%');
    expect(media.height).toBe('100%');
    // `contain`, not `cover`: the platform letterboxes anything that is not
    // the screen's own shape, and cropping the user's frame would misreport
    // what actually gets posted.
    expect(media.objectFit).toBe('contain');
  });

  it('lays a gallery image out the same way', () => {
    openGallery();
    expect(window.getComputedStyle(stage()).position).toBe('absolute');
    const media = window.getComputedStyle(
      document.querySelector('.pv-im .pv-stage img') as HTMLElement,
    );
    expect(media.width).toBe('100%');
    expect(media.height).toBe('100%');
    expect(media.objectFit).toBe('contain');
  });

  /** The interface floats over the content: strictly above it in the stack. */
  it('floats the interface above the content instead of beside it', () => {
    openVideo();
    const z = (sel: string) =>
      Number(window.getComputedStyle(document.querySelector(sel) as HTMLElement).zIndex);
    expect(z('.pv-im .pv-stage')).toBe(1);
    expect(z('.pv-im-top')).toBe(5);
    expect(z('.pv-im-rail')).toBe(5);
    expect(z('.pv-im .pv-caption')).toBe(5);
    expect(z('.pv-im-nav')).toBe(5);
    expect(z('.pv-im .pv-pager')).toBe(6);
  });

  /**
   * ⚠️ THE ONE THAT CATCHES THE COPY-PASTE.
   *
   * The two-column feed's tab row is 精选 / 关注 / 推荐 with 精选 selected; this
   * screen's is 同城 / 关注 / 推荐 with 推荐 selected. Two words out of three are
   * shared, so reusing the wrong constant produces a screen that looks almost
   * right — which is why the exact row AND the exact selection are pinned here
   * rather than merely "some tabs are drawn".
   */
  it('draws this screen’s own tab row, not the two-column feed’s', () => {
    openVideo();
    const top = document.querySelector('.pv-im-top') as HTMLElement;
    expect(Array.from(top.querySelectorAll('span')).map((n) => n.textContent))
      .toEqual(['同城', '关注', '推荐']);
    expect((top.querySelector('.cur') as HTMLElement).textContent).toBe('推荐');
  });

  it('draws the app’s bottom navigation', () => {
    openVideo();
    const nav = document.querySelector('.pv-im-nav') as HTMLElement;
    expect(Array.from(nav.querySelectorAll('span')).map((n) => n.textContent))
      .toEqual(['首页', '朋友', '＋', '消息', '我']);
  });

  /**
   * ⚠️ THE FABRICATION GUARD.
   *
   * Douyin puts a like / comment / favourite count beside every rail icon.
   * We do not have those numbers, and an unpublished post does not have them
   * either — `0` reads as a measurement of something that was never measured.
   *
   * Asserted as an equality on the WHOLE screen's text rather than as "no
   * digits in the rail", for two reasons: a `not.toContain` passes on a screen
   * that renders nothing, and a count could grow back anywhere on the overlay,
   * not only where it used to sit. Every character below is either the
   * platform's own furniture, the user's own material, or our pager.
   */
  it('puts exactly the user’s own material on the screen and invents nothing', () => {
    openVideo({ title: 'Studio tour', handle: 'realaccount' });
    expect(immersive().textContent).toBe(
      '同城关注推荐@realaccountStudio tour原声1 / 1首页朋友＋消息我',
    );
    // And the rail really did render, so the equality above is a statement
    // about what its icons carry rather than about a rail that is missing.
    const rail = document.querySelector('.pv-im-rail') as HTMLElement;
    expect(rail.querySelectorAll('svg').length).toBe(5);
  });

  /** The avatar is the account's real one, or nothing at all. */
  it('shows the account’s own avatar, and draws no stand-in when it has none', () => {
    const { update } = openVideo({ handle: 'realaccount', avatarUrl: '/avatar/real' });
    const rail = () => document.querySelector('.pv-im-rail') as HTMLElement;
    expect(rail().querySelectorAll('img').length).toBe(1);
    expect(rail().querySelector('img')?.getAttribute('src')).toBe('/avatar/real');

    update({ avatarUrl: null });
    // Paired with the glyph count so "no avatar" cannot be satisfied by a rail
    // that stopped rendering altogether.
    expect(rail().querySelectorAll('img').length).toBe(0);
    expect(rail().querySelectorAll('svg').length).toBe(5);
  });

  /**
   * The sound line, all four ways it can end.
   *
   * The unconditional "Original sound · @handle" this panel used to print is
   * one of the fabrications the component exists to prevent: the page has a
   * music panel, so the moment a track is picked that line is simply false.
   */
  describe('the sound line', () => {
    const caption = (): HTMLElement =>
      document.querySelector('.pv-im .pv-caption') as HTMLElement;

    it('names the picked track, author and all', () => {
      openVideo({
        title: 'Studio tour',
        handle: 'realaccount',
        soundtrack: { kind: 'track', label: 'Sunset Drive - Kite Club' },
      });
      expect(caption().textContent).toBe('@realaccountStudio tourSunset Drive - Kite Club');
    });

    it('says the platform’s own word when the music control is left alone', () => {
      openVideo({ title: 'Studio tour', handle: 'realaccount' });
      expect(caption().textContent).toBe('@realaccountStudio tour原声');
    });

    /**
     * A keyword with no track picked is the case nobody can answer: the
     * browser types it into the platform's own dialog and whatever comes back
     * gets attached, and one search really does return several
     * character-identical titles under different ids. Printing the keyword
     * would name a track we have not got.
     */
    it('says nothing when a keyword was typed but no track picked', () => {
      openVideo({
        title: 'Studio tour',
        handle: 'realaccount',
        soundtrack: { kind: 'unresolved' },
      });
      expect(caption().textContent).toBe('@realaccountStudio tour');
    });

    /**
     * "Leave the music control alone" means a video keeps its own recorded
     * audio. A gallery has no recorded audio to keep, so what ends up on one
     * is not something this page knows.
     */
    it('says nothing on an image post with no track picked', () => {
      openGallery({ title: 'Studio tour', handle: 'realaccount' });
      expect(caption().textContent).toBe('@realaccountStudio tour');
    });
  });

  /**
   * ⚠️ The chrome is a depiction of ONE app. Drawing Douyin's furniture around
   * a Xiaohongshu post would be a fabrication of a different app's interface —
   * the same defect as an invented count, wearing a hat.
   */
  it('draws no platform furniture at all when the post is not going to Douyin', () => {
    openVideo({ platform: 'xiaohongshu', title: 'Studio tour', handle: 'realaccount' });
    // The user's own material still renders — asserted first, so the emptiness
    // below is a statement about the chrome and not about a blank panel.
    expect(immersive().textContent).toBe('@realaccountStudio tour1 / 1');
    expect(document.querySelectorAll('.pv-im-top').length).toBe(0);
    expect(document.querySelectorAll('.pv-im-rail').length).toBe(0);
    expect(document.querySelectorAll('.pv-im-nav').length).toBe(0);
  });

  it('tells the reader which parts of the screen are a sketch', () => {
    openVideo();
    expect(screen.getByText(
      'The tabs, side icons and bottom bar are a sketch of the platform app, with no counts because this post has none. Only the outlined pager belongs to this page.',
    )).toBeTruthy();
  });

  it('names the pager as ours rather than letting it pass for the platform’s', () => {
    openVideo({ items: [CLIP, CLIP_2] });
    const pagerGroup = screen.getByRole('group', {
      name: 'Preview control (part of this page, not the platform)',
    });
    expect(within(pagerGroup).getByText('1 / 2')).toBeTruthy();
  });

  /**
   * ══ THE CONTROL BAR APPEARS ON INTERACTION ════════════════════════════════
   *
   * The bar stays NATIVE — a hand-rolled scrubber is drag behaviour, and jsdom
   * has no pointer geometry, so hand-rolling would move the most breakable
   * part of this panel outside the gate entirely. But a bar that is always up
   * (play button, volume slider, overflow menu) is most of what made the old
   * preview read as a player rather than as the post, so it is revealed on
   * interaction rather than removed.
   *
   * The reveal rule itself is plain DOM events, so it IS testable, and every
   * case below asserts BOTH states — a rule that only ever turned the bar on
   * would satisfy half of each pair.
   */
  describe('the player’s control bar', () => {
    const hasBar = () =>
      (document.querySelector('.pv-im .pv-video') as HTMLVideoElement).hasAttribute('controls');

    it('is down until the reader points at the screen, and goes back down after', () => {
      openVideo();
      expect(hasBar()).toBe(false);

      fireEvent.mouseOver(immersive());
      expect(hasBar()).toBe(true);

      fireEvent.mouseOut(immersive());
      expect(hasBar()).toBe(false);
    });

    it('comes up for a tap, for readers with no pointer to hover with', () => {
      openVideo();
      expect(hasBar()).toBe(false);

      fireEvent.click(immersive());
      expect(hasBar()).toBe(true);
    });

    /** The keyboard route. `tabIndex` on the player is what makes it exist. */
    it('comes up when the player takes keyboard focus, and goes down on blur', () => {
      openVideo();
      const el = document.querySelector('.pv-im .pv-video') as HTMLVideoElement;
      expect(hasBar()).toBe(false);

      fireEvent.focus(el);
      expect(hasBar()).toBe(true);

      fireEvent.blur(el);
      expect(hasBar()).toBe(false);
    });

    /**
     * The native bar is painted inside the player, i.e. under every overlay,
     * and the bottom navigation is the only decoration low enough to cover it.
     * A real control the reader asked for outranks a sketch of somebody
     * else's, so the sketch steps aside while the bar is up.
     */
    it('gets the bottom navigation and the caption out of its way while it is up', () => {
      openVideo();
      const nav = () => document.querySelector('.pv-im-nav') as HTMLElement;
      const caption = () => document.querySelector('.pv-im .pv-caption') as HTMLElement;
      expect(window.getComputedStyle(nav()).opacity).toBe('1');
      expect(window.getComputedStyle(caption()).bottom).toBe('44px');

      fireEvent.mouseOver(immersive());
      expect(window.getComputedStyle(nav()).opacity).toBe('0');
      expect(window.getComputedStyle(caption()).bottom).toBe('56px');
    });
  });
});
