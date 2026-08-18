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
 * ⚠️ jsdom resolves the CSS cascade but does NOT do layout: every
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
  covers: null,
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

const grid = (): HTMLElement => screen.getByLabelText(
  'Profile grid — your post is the first cell; the eight cells around it are empty placeholders.',
);

const tab = (name: string): HTMLElement => screen.getByRole('tab', { name });

describe('the stylesheet under test is actually loaded', () => {
  it('resolves a rule that only this stylesheet defines', () => {
    renderPanel();
    // If the <style> injection above ever breaks, this reads '' and the whole
    // file's style assertions become vacuous — so it is checked directly.
    expect(getComputedStyle(screen.getByRole('tablist')).display).toBe('flex');
  });
});

describe('the nine-cell grid', () => {
  it('is three columns of nine cells, one real and eight empty', () => {
    renderPanel({ items: [CLIP], covers: { vertical: 'cv', horizontal: 'ch' } });

    const cells = grid().querySelectorAll('.pv-cell');
    expect(cells.length).toBe(9);
    expect(getComputedStyle(grid()).gridTemplateColumns).toBe('repeat(3, 1fr)');

    // The user's own post is the FIRST cell — the whole point of the layout.
    expect(cells[0].classList.contains('mine')).toBe(true);
    expect(cells[0].querySelectorAll('img').length).toBe(1);

    /* DOM order alone does not pin "top-left": in a grid, `order` and `dir`
       both reposition a child without touching the markup, and jsdom cannot
       see where anything actually lands. So the two properties that could
       move it are asserted at their neutral values — positively, so a missing
       stylesheet cannot satisfy them by returning ''. Together with the three
       equal columns above, first-in-DOM then really is top-left.
       (Verified visually in a real browser; see the PR body.) */
    expect(getComputedStyle(cells[0]).order).toBe('0');
    expect(getComputedStyle(grid()).direction).toBe('ltr');

    // The other eight are placeholders. Asserted as a count of zero images
    // across all of them, which is a number that changes the moment somebody
    // wires this grid to real posts.
    const others = Array.from(cells).slice(1);
    expect(others.length).toBe(8);
    expect(others.filter((c) => c.classList.contains('empty')).length).toBe(8);
    expect(others.filter((c) => c.getAttribute('aria-hidden') === 'true').length).toBe(8);
    expect(others.reduce((n, c) => n + c.querySelectorAll('img').length, 0)).toBe(0);
  });

  /**
   * The real-browser measurements in the PR body were taken against a static
   * page reproducing this nesting. That evidence only transfers to the shipped
   * component if the component really emits it, so the chain is asserted here:
   * phone shell → screen → grid → cells, each a DIRECT child of the last.
   * Every rule that positions the grid is written against that chain.
   */
  it('nests the grid inside the phone screen, which the CSS rules assume', () => {
    const { container } = renderPanel();
    expect(container.querySelectorAll('.phone > .pv-screen > .pv-grid').length).toBe(1);
    expect(container.querySelectorAll('.pv-grid > .pv-cell').length).toBe(9);
    expect(container.querySelectorAll('.pv-cell.mine > .pv-cell-title').length).toBe(1);
  });

  it('says in words that the neighbours are placeholders', () => {
    renderPanel();
    expect(
      screen.getByText('The surrounding cells are empty placeholders, not real posts.'),
    ).toBeTruthy();
  });

  /**
   * The regression guard for the defect this panel was rebuilt to remove: the
   * card it replaces rendered `0` beside a heart and `0` beside a speech
   * bubble, plus an unconditional "Original sound" line.
   *
   * Asserted as an EQUALITY on the grid's entire text content rather than as a
   * `not.toContain('0')`: an equality fails both when a fabricated number
   * comes back AND when the grid renders nothing at all.
   */
  it('shows the title and nothing else — no counts, no invented metadata', () => {
    renderPanel({ title: 'Rooftop timelapse', items: [CLIP] });
    expect(grid().textContent).toBe('Rooftop timelapse');
  });

  it('marks an empty title as a placeholder instead of leaving a blank strip', () => {
    renderPanel({ title: '' });
    const caption = grid().querySelector('.pv-cell-title');
    expect(caption?.textContent).toBe('Your title appears here');
    expect(caption?.classList.contains('ph')).toBe(true);
  });
});

describe('the vertical / horizontal switch', () => {
  it('draws 3:4 cells for vertical and 4:3 cells for horizontal', () => {
    const { update } = renderPanel({ orientation: 'vertical' });
    expect(getComputedStyle(grid().querySelector('.pv-cell') as Element).aspectRatio)
      .toBe('3 / 4');

    update({ orientation: 'horizontal' });
    expect(getComputedStyle(grid().querySelector('.pv-cell') as Element).aspectRatio)
      .toBe('4 / 3');
  });

  it('shows the matching cover crop, not just a differently shaped box', () => {
    const { update } = renderPanel({
      orientation: 'vertical',
      covers: { vertical: 'cover-v', horizontal: 'cover-h' },
    });
    expect(grid().querySelector('img')?.getAttribute('src')).toBe('/file/cover-v');

    update({ orientation: 'horizontal' });
    expect(grid().querySelector('img')?.getAttribute('src')).toBe('/file/cover-h');
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

  it('falls back to the thumbnail AND says it is not a cover', () => {
    renderPanel({ covers: null, items: [CLIP] });
    expect(grid().querySelector('img')?.getAttribute('src')).toBe('/thumb/clip');
    expect(screen.getByText(
      'No cover set — this is the stored thumbnail, not a cover. The platform picks its own frame at publish time.',
    )).toBeTruthy();
  });

  it('states the absence rather than drawing an empty box', () => {
    renderPanel({ covers: null, items: [] });
    expect(within(grid()).getByText('No cover yet')).toBeTruthy();
  });

  it('uses the first image for an image post and says so', () => {
    renderPanel({ kind: 'images', items: [IMAGE_A, IMAGE_B] });
    expect(grid().querySelector('img')?.getAttribute('src')).toBe('/thumb/a');
    expect(screen.getByText('Image posts take their cover from the first image.')).toBeTruthy();
  });

  it('signs the file URL with the session token when there is one', () => {
    renderPanel({ covers: { vertical: 'cover-v', horizontal: 'cover-h' }, mediaToken: 'jwt' });
    expect(grid().querySelector('img')?.getAttribute('src')).toBe('/file/cover-v?token=jwt');
  });
});

describe('a picture that fails to load says so', () => {
  it('replaces the cell with a stated failure instead of an empty box', () => {
    renderPanel({ covers: { vertical: 'cover-v', horizontal: 'cover-h' } });
    fireEvent.error(grid().querySelector('img') as Element);
    expect(within(grid()).getByText('This image could not be loaded.')).toBeTruthy();
  });

  it('re-tries when the URL changes, rather than staying failed forever', () => {
    const { update } = renderPanel({ covers: { vertical: 'cover-v', horizontal: 'cover-h' } });
    fireEvent.error(grid().querySelector('img') as Element);
    expect(within(grid()).getByText('This image could not be loaded.')).toBeTruthy();

    // A token arriving late produces a different URL — that attempt deserves
    // its own verdict.
    update({ mediaToken: 'jwt' });
    expect(grid().querySelector('img')?.getAttribute('src')).toBe('/file/cover-v?token=jwt');
  });
});

describe('tab availability follows the post type', () => {
  it('offers the gallery to image posts and refuses it to video posts', () => {
    const { update } = renderPanel({ kind: 'video' });
    expect(tab('Gallery preview')).toBeDisabled();
    expect(tab('Gallery preview').title).toBe('This is a video post — there is no image gallery.');

    update({ kind: 'images', items: [IMAGE_A] });
    expect(tab('Gallery preview')).toBeEnabled();
  });

  it('refuses the video tab to image posts and offers it to video posts', () => {
    const { update } = renderPanel({ kind: 'images', items: [IMAGE_A] });
    expect(tab('Video preview')).toBeDisabled();
    expect(tab('Video preview').title).toBe('This is an image post — there is no video to play.');

    update({ kind: 'video', items: [CLIP] });
    expect(tab('Video preview')).toBeEnabled();
  });

  it('keeps the cover tab open for both, and starts there', () => {
    const { update } = renderPanel({ kind: 'video' });
    expect(tab('Cover & title')).toBeEnabled();
    expect(tab('Cover & title').getAttribute('aria-selected')).toBe('true');

    update({ kind: 'images', items: [IMAGE_A] });
    expect(tab('Cover & title')).toBeEnabled();
  });

  it('moves the reader off a tab that stops applying', () => {
    const { update } = renderPanel({ kind: 'images', items: [IMAGE_A, IMAGE_B] });
    fireEvent.click(tab('Gallery preview'));
    expect(tab('Gallery preview').getAttribute('aria-selected')).toBe('true');

    update({ kind: 'video', items: [CLIP] });
    expect(tab('Cover & title').getAttribute('aria-selected')).toBe('true');
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

  it('gives the user a seek bar, and does not start on its own', () => {
    openVideo();
    const el = player();
    /* Native controls ARE the draggable progress bar. jsdom cannot drag one —
       no layout means no pointer geometry — so what is pinned here is that the
       control surface is present and that playback is not automatic. Actual
       dragging was exercised in a real browser; see the PR body. */
    expect(el.hasAttribute('controls')).toBe(true);
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

  it('stops when the post turns into an image post', () => {
    const { update } = openVideo();
    expect(pauseSpy.mock.calls.length).toBe(0);

    update({ kind: 'images', items: [IMAGE_A] });
    expect(pauseSpy.mock.calls.length).toBe(1);
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
