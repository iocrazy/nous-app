/**
 * Candidate frames are transient, not resources.
 *
 * WHAT CHANGED: each sampled frame used to be persisted as its own `derived`
 * image row that inherited the source video's folder, so picking a cover
 * littered the user's Library with frames nothing could distinguish from real
 * material (`source_type` is identical to a canvas derive; only the filename
 * hinted, and guessing identity from a filename is not a thing this repo does).
 * Now the preview rides inline as a data URL and the pick posts the frame's
 * `timestamp_seconds` so the server re-reads that exact frame.
 *
 * This file pins the three consequences that live in the component:
 *   1. tiles render from `preview_data_url` — no resource fetch at all
 *   2. picking sends the COORDINATE (source video + offset), not an id
 *   3. the new failure shapes stay user-visible and say the right thing
 *
 * ⚠️ EVERY ASSERTION IS POSITIVE. "no resource URL was requested" alone would
 * pass vacuously if the strip never rendered, so each such check is paired
 * with proof that the tiles really are on screen.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import enJson from '../../public/locales/en.json';
import type { CoverCandidate } from '../../types';

const { extractCoverFrames, selectCoverFrame } = vi.hoisted(() => ({
  extractCoverFrames: vi.fn(),
  selectCoverFrame: vi.fn(),
}));

vi.mock('../../services/distributionService', () => ({
  extractCoverFrames,
  selectCoverFrame,
}));

/** Counts every attempt to build a resource URL. A candidate tile must never
 *  need one — if this ever goes above zero again, frames are being fetched,
 *  which means something persisted them. */
const { resourceUrl } = vi.hoisted(() => ({ resourceUrl: vi.fn() }));
vi.mock('../../services/resourceService', () => ({
  getResourceFileUrl: (id: string, token?: string) => {
    resourceUrl(id, token);
    return `/file/${id}`;
  },
}));

const { realtime } = vi.hoisted(() => ({
  realtime: {
    updateHandler: null as ((p: { new: unknown }) => void) | null,
    subscribeCb: null as ((s: string) => void) | null,
    seed: { data: null as unknown, error: null as unknown },
  },
}));

vi.mock('../../supabaseClient', () => {
  const channelObj = {
    on: (_evt: string, _cfg: unknown, cb: (p: { new: unknown }) => void) => {
      realtime.updateHandler = cb;
      return channelObj;
    },
    subscribe: (cb: (s: string) => void) => {
      realtime.subscribeCb = cb;
      return channelObj;
    },
  };
  return {
    getSupabaseClient: () => ({
      auth: { getSession: () => Promise.resolve({ data: { session: { access_token: 'jwt' } } }) },
      channel: () => channelObj,
      removeChannel: vi.fn(),
      from: () => ({
        select: () => ({ eq: () => ({ maybeSingle: () => Promise.resolve(realtime.seed) }) }),
      }),
    }),
  };
});

import { CoverPicker } from './CoverPicker';

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  return inst;
};

/** The real `metadata.cover_frames` shape the workflow writes — a preview data
 *  URL plus the offset, and deliberately NO resource id. */
const CANDIDATES: CoverCandidate[] = [
  {
    index: 0,
    timestamp_seconds: 1.5,
    preview_data_url: 'data:image/jpeg;base64,AAAA',
    preview_width: 240,
    preview_height: 427,
  },
  {
    index: 1,
    timestamp_seconds: 72.25,
    preview_data_url: 'data:image/jpeg;base64,BBBB',
    preview_width: 240,
    preview_height: 427,
  },
];

const renderPicker = (onChange = vi.fn()) => {
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <div className="dist-v4">
        <CoverPicker
          sources={[{ id: '30', filename: 'launch-cut.mp4' }]}
          value={null}
          onChange={onChange}
        />
      </div>
    </I18nextProvider>,
  );
  return { ...utils, onChange };
};

/** Walk the component to the "candidates on screen" state the way a user does:
 *  press the button, let the POST resolve, then push the Realtime row. */
const sampleAndDeliver = async (
  meta: Record<string, unknown>,
  phase = 'completed',
) => {
  fireEvent.click(screen.getByRole('button', { name: /Pick a frame from the video/i }));
  await waitFor(() => expect(extractCoverFrames).toHaveBeenCalled());
  await waitFor(() => expect(realtime.updateHandler).not.toBeNull());
  await act(async () => {
    realtime.updateHandler?.({
      new: { phase, metadata: { cover_frames: { source_resource_id: '30', candidates: [], ...meta } } },
    });
  });
};

describe('cover candidates are transient, not library resources', () => {
  beforeEach(() => {
    extractCoverFrames.mockReset().mockResolvedValue({ task_id: 'wf-1' });
    selectCoverFrame.mockReset().mockResolvedValue({
      cover_vertical_resource_id: 'cv-1',
      cover_horizontal_resource_id: 'ch-1',
    });
    resourceUrl.mockClear();
    realtime.updateHandler = null;
    realtime.subscribeCb = null;
    realtime.seed = { data: null, error: null };
  });

  it('renders each tile straight from its inline preview, fetching no resource', async () => {
    renderPicker();
    await sampleAndDeliver({ candidates: CANDIDATES });

    const tiles = await screen.findAllByRole('radio');
    expect(tiles).toHaveLength(2);
    // Positive: the src really is the inline preview the workflow sent.
    const sources = tiles.map((t) => t.querySelector('img')?.getAttribute('src'));
    expect(sources).toEqual([
      'data:image/jpeg;base64,AAAA',
      'data:image/jpeg;base64,BBBB',
    ]);
    // The timestamps are on screen, so the tiles are the real ones.
    expect(screen.getByText('0:01')).toBeInTheDocument();
    expect(screen.getByText('1:12')).toBeInTheDocument();
    // Only now is "no resource URL was built" a meaningful statement: the
    // strip demonstrably rendered, and still nothing had to be fetched.
    expect(resourceUrl).not.toHaveBeenCalled();
  });

  it('posts the picked coordinate — the source video plus its offset', async () => {
    const { onChange } = renderPicker();
    await sampleAndDeliver({ candidates: CANDIDATES });

    fireEvent.click((await screen.findAllByRole('radio'))[1]);

    await waitFor(() =>
      expect(selectCoverFrame).toHaveBeenCalledWith({
        source_resource_id: '30',
        timestamp_seconds: 72.25,
      }));
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith({ vertical: 'cv-1', horizontal: 'ch-1' }));
  });

  it('samples from the video the frames belong to, not the one now selected', async () => {
    // The dropdown can move while a strip from the previous video is still on
    // screen. Cropping against the newly selected video would silently produce
    // a cover from footage the user never looked at.
    renderPicker();
    await sampleAndDeliver({ source_resource_id: '99', candidates: CANDIDATES });

    fireEvent.click((await screen.findAllByRole('radio'))[0]);

    await waitFor(() =>
      expect(selectCoverFrame).toHaveBeenCalledWith({
        source_resource_id: '99',
        timestamp_seconds: 1.5,
      }));
  });

  it('says the read-back timed out instead of blaming the frame', async () => {
    // A 504 here means "the source had to be fetched again and that took too
    // long" — the same frame usually works on a second try. Telling the user
    // to pick another frame sends them chasing a problem that is not theirs.
    selectCoverFrame.mockRejectedValueOnce(Object.assign(new Error('slow'), { status: 504 }));
    const { onChange } = renderPicker();
    await sampleAndDeliver({ candidates: CANDIDATES });

    fireEvent.click((await screen.findAllByRole('radio'))[0]);

    expect(await screen.findByText(/Reading that frame back took too long/i))
      .toBeInTheDocument();
    // The failure must not leave the page claiming a cover is attached.
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(null));
  });

  it('refuses to post a coordinate it cannot stand behind', async () => {
    // A candidate whose timestamp did not survive the jsonb round-trip cannot
    // be re-read. Posting it anyway would crop some other frame or 400 — both
    // read to the user as "the tile did nothing".
    renderPicker();
    await sampleAndDeliver({
      candidates: [{ ...CANDIDATES[0], timestamp_seconds: null as unknown as number }],
    });

    fireEvent.click((await screen.findAllByRole('radio'))[0]);

    expect(await screen.findByText(/That frame cannot be used/i)).toBeInTheDocument();
    expect(selectCoverFrame).not.toHaveBeenCalled();
  });

  it('still reports a workflow failure with the server-authored reason', async () => {
    renderPicker();
    await sampleAndDeliver(
      { error: 'could not read this video\'s duration', error_status: 422 },
      'failed',
    );

    expect(await screen.findByText(/could not read this video's duration/i))
      .toBeInTheDocument();
  });
});
