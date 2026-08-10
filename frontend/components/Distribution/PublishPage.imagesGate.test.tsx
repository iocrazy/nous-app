/**
 * The Images tab, against the REAL capability table (./capabilities is
 * deliberately NOT mocked here — that is the whole point of this file).
 *
 * Backstory: three layers claimed image posts and disagreed. The page offered
 * an "Images" tab, the backend profile listed `images` in `content_types`, and
 * the browser service — the only layer that actually posts — refused it with
 * `unsupported_content_type`. So the user picked images, uploaded them, filled
 * in the title, submitted, waited in the queue, and only then found out.
 *
 * The refusal was right; its timing was the defect. These tests assert the
 * user-visible half of moving it to the front: with a Douyin account connected,
 * the tab is dead before anything is filled in, and it says why.
 *
 * The sibling file PublishPage.test.tsx mocks ./capabilities so Douyin CAN take
 * images — that keeps the gallery/upload coverage alive for P2-1 step 2 and
 * proves this gate is driven by that table rather than hardcoded off.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect } from 'vitest';

const { createPublishTask } = vi.hoisted(() => ({
  createPublishTask: vi.fn().mockResolvedValue({ id: '700', accounts: [] }),
}));

vi.mock('../../services/distributionService', () => ({
  listAccounts: vi.fn().mockResolvedValue([
    { id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
      auth_type: 'session', token_expires_at: null, status: 'active',
      created_at: '2026-08-08T00:00:00Z' },
  ]),
  listLibraryMedia: vi.fn((_scope: string, opts?: { mediaType?: string }) =>
    Promise.resolve(
      opts?.mediaType === 'image'
        ? [{ id: 'img-1', filename: 'photo-a.jpg', thumbnail_url: null }]
        : [{ id: '30', filename: 'clip-a.mp4', thumbnail_url: null }],
    ),
  ),
  listGeneratedVideos: vi.fn().mockResolvedValue([]),
  promoteGeneratedVideo: vi.fn(),
  createPublishTask,
  extractCoverFrames: vi.fn(),
  selectCoverFrame: vi.fn(),
}));

vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn().mockResolvedValue({ id: 'tag-1', name: 'To Publish' }),
  addResourceTag: vi.fn().mockResolvedValue(undefined),
  removeResourceTag: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../services/resourceService', () => ({
  uploadResource: vi.fn(),
  getGalleryItems: vi.fn().mockResolvedValue([]),
  getResourceCoverUrl: (id: string) => `/cover/${id}`,
  getResourceFileUrl: (id: string) => `/file/${id}`,
  GALLERY_MIME: 'application/x-mediahub-gallery',
}));

vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => vi.fn(),
}));

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

vi.mock('../../supabaseClient', () => {
  const channelObj = {
    on: () => channelObj,
    subscribe: () => channelObj,
  };
  return {
    getSupabaseClient: () => ({
      auth: { getSession: () => Promise.resolve({ data: { session: { access_token: 'jwt' } } }) },
      channel: () => channelObj,
      removeChannel: vi.fn(),
      from: () => ({
        select: () => ({ eq: () => ({ maybeSingle: () => Promise.resolve({ data: null, error: null }) }) }),
      }),
    }),
  };
});

vi.mock('../../contexts/TeamContext', () => ({
  useTeamContext: () => ({ personalTeamId: 'pt1' }),
}));

import PublishPage from './PublishPage';

const imagesTab = () => screen.getByRole('tab', { name: /^Images$/ });

describe('PublishPage — image posts are refused up front, not at the last step', () => {
  it('disables the Images tab for a Douyin account and says why', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    expect(imagesTab()).toBeDisabled();
    // A greyed-out control with no explanation reads as a bug. The reason is
    // on the card, not only in a title attribute nobody hovers.
    expect(screen.getByText(/Image posts are not supported yet/i)).toBeInTheDocument();
    // Video is still the live choice — this gate removes a claim, not a page.
    expect(screen.getByRole('tab', { name: /^Video$/ })).not.toBeDisabled();
  });

  it('stays on Video when the disabled tab is clicked anyway', async () => {
    // fireEvent.click on a disabled button is a no-op in the DOM, so this also
    // covers the second lock inside onContentTypeChange: whichever way the
    // click arrives, the page must not enter images mode.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(imagesTab());

    expect(screen.getByRole('tab', { name: /^Video$/ })).toHaveAttribute('aria-selected', 'true');
    expect(imagesTab()).toHaveAttribute('aria-selected', 'false');
    // The content card still counts videos rather than images — i.e. the
    // load() effect never switched to mediaType 'image'. (The count itself is
    // an i18n placeholder here: no i18n instance is initialised in tests, so
    // t() returns the raw default string.)
    expect(screen.getByText(/^Video ·/)).toBeInTheDocument();
    expect(screen.queryByText(/^Images ·/)).toBeNull();
  });

  it('still publishes video end to end', async () => {
    // The gate must not cost anything the user could actually do today.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Launch day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(createPublishTask.mock.calls.at(-1)?.[0]).toMatchObject({ content_type: 'video' });
  });
});
