import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect } from 'vitest';

// vi.mock is hoisted above top-level consts, so the fns it references must be
// hoisted too (vi.hoisted) — otherwise "Cannot access before initialization".
const { createPublishTask, promoteGeneratedVideo, uploadResource, getGalleryItems } = vi.hoisted(() => ({
  createPublishTask: vi.fn().mockResolvedValue({ id: '700', accounts: [] }),
  promoteGeneratedVideo: vi.fn().mockResolvedValue('900'),
  uploadResource: vi.fn(),
  getGalleryItems: vi.fn().mockResolvedValue([]),
}));

// Stable toast spy so the upload-failure test can assert on it.
const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));

vi.mock('../../services/distributionService', () => ({
  listAccounts: vi.fn().mockResolvedValue([
    // Two bindings of the same platform, because the row copy differs by
    // auth_type: a QR-bound account publishes unattended, an OAuth one needs
    // the user's phone.
    { id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
      auth_type: 'session',
      token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '12', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op3', username: 'OAuth One', avatar_url: null,
      auth_type: 'oauth',
      token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '11', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op2', username: 'Expired One', avatar_url: null,
      auth_type: 'oauth',
      token_expires_at: null, status: 'expired', created_at: '2026-07-08T00:00:00Z' },
  ]),
  // Returns images when the caller asks for mediaType 'image', videos otherwise
  // — mirrors the real backend `types=` filter so content-switch tests are real.
  listLibraryMedia: vi.fn((_scope: string, opts?: { mediaType?: string }) =>
    Promise.resolve(
      opts?.mediaType === 'image'
        ? [
            { id: 'img-1', filename: 'photo-a.jpg', thumbnail_url: null },
            { id: 'img-2', filename: 'photo-b.jpg', thumbnail_url: null },
            { id: 'img-3', filename: 'photo-c.jpg', thumbnail_url: null },
            // A first-class gallery entity row (images mode also requests
            // `types=gallery`). Its own id is never published — picking it
            // expands into child image ids.
            { id: 'gal-1', filename: 'my-gallery', thumbnail_url: null,
              mime_type: 'application/x-mediahub-gallery', gallery_count: 2 },
          ]
        : [{ id: '30', filename: 'clip-a.mp4', thumbnail_url: null }],
    ),
  ),
  listGeneratedVideos: vi.fn().mockResolvedValue([
    { id: '77', name: 'Sunset drone shot', created_at: '2026-07-10T00:00:00Z',
      promoted_resource_id: null },
  ]),
  promoteGeneratedVideo,
  createPublishTask,
}));

// Tag plumbing behind the "To publish" mark — inert defaults.
vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn().mockResolvedValue({ id: 'tag-1', name: 'To Publish' }),
  addResourceTag: vi.fn().mockResolvedValue(undefined),
  removeResourceTag: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => vi.fn(),
}));

// PublishPage calls useToast — mock it so the test needn't wrap ToastProvider.
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

// Inline image upload path (resourceService.uploadResource) + gallery expand.
vi.mock('../../services/resourceService', () => ({
  uploadResource,
  getGalleryItems,
  getResourceCoverUrl: (id: string) => `/cover/${id}`,
  GALLERY_MIME: 'application/x-mediahub-gallery',
}));

// PublishPage derives scope via useWorkspaceScope → useTeamContext, which throws
// without a TeamProvider. Mock the context so the hook resolves a scope id.
vi.mock('../../contexts/TeamContext', () => ({
  useTeamContext: () => ({ personalTeamId: 'pt1' }),
}));

import PublishPage from './PublishPage';

describe('PublishPage', () => {
  it('publishes only after content + account + title are chosen', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    // The Library is no longer flooded into the content card — it only appears
    // inside the "Add from Library" picker. Wait for accounts to load first.
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    const publishBtn = screen.getByRole('button', { name: /Publish now/i });
    expect(publishBtn).toBeDisabled();

    // Open the picker, choose a video, then close it. (The tile's accessible
    // name includes the bookmark toggle's label, so match by substring.)
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));                    // pick account
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Launch day' },
    });
    expect(publishBtn).not.toBeDisabled();

    fireEvent.click(publishBtn);
    await waitFor(() => expect(createPublishTask).toHaveBeenCalledTimes(1));
    const arg = createPublishTask.mock.calls[0][0];
    expect(arg.resource_ids).toEqual(['30']);
    expect(arg.account_ids).toEqual(['10']);
    expect(arg.title).toBe('Launch day');
  });

  it('collects topics and submits them in the payload', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    // Open the topic input via the "# Topic" chip, type tags (Enter commits;
    // a trending chip adds one too), then run the standard publish flow.
    fireEvent.click(screen.getByRole('button', { name: /# Topic/i }));
    const topicInput = screen.getByLabelText(/Add a topic/i);
    fireEvent.change(topicInput, { target: { value: '#goldenhour' } });
    fireEvent.keyDown(topicInput, { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: '#cityscape' }));

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Topic day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    // Leading '#' is stripped; both the typed and trending tag are present.
    expect(arg.topics).toEqual(['goldenhour', 'cityscape']);
  });

  it('states per account whether publishing needs the user afterwards', async () => {
    // Replaces an older test that asserted the channel picker existed. The
    // picker is gone: a channel only works for accounts bound the matching way,
    // so offering the choice mostly offered a way to pick a broken combination.
    // What the user actually needs to know is per row — whether pressing
    // Publish finishes the job, or leaves them a link to confirm on a phone.
    // That difference is the entire reason someone binds by QR code.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    expect(screen.queryByRole('button', { name: /Official API/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /H5 share/i })).toBeNull();

    expect(screen.getByText(/Publishes unattended/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Needs confirming on your phone/i).length).toBeGreaterThan(0);
  });

  it('asks for the session route and lets the backend downgrade per account', async () => {
    // The page always requests 'session'. That is not a claim that every
    // account can do it — `decide_channel` re-decides per account and sends an
    // OAuth-bound one down the H5 handoff. Requesting the weaker 'h5' instead
    // would be the lossy direction: it would strand session accounts on a route
    // that needs a human.
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Route check' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    expect(createPublishTask.mock.calls[0][0]).toMatchObject({ channel: 'session' });
  });

  it('promotes a generated video on pick and publishes its resource id', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    // Switch to the Generated tab and pick the generation — first pick
    // promotes it into the Library and selects the resulting resource id.
    fireEvent.click(await screen.findByRole('button', { name: /^Generated$/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Sunset drone shot/ }));
    await waitFor(() => expect(promoteGeneratedVideo).toHaveBeenCalledWith('77'));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Gen launch' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));
    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.resource_ids).toEqual(['900']);
  });

  it('marks expired accounts non-selectable', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Expired One')).toBeInTheDocument());
    // The account name ("Expired One") and the status subtitle both match
    // /Expired/i, so assert on the unambiguous reauthorize prompt instead.
    expect(screen.getByText(/reauthorize/i)).toBeInTheDocument();
  });

  it('switches to Images mode and publishes a gallery in pick order', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    // Flip the content type to Images — the picker now lists image media.
    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    // Generated media is video-only — the tab must be gone in images mode.
    expect(screen.queryByRole('button', { name: /^Generated$/ })).toBeNull();
    // Pick photo-b BEFORE photo-a to prove the gallery order follows the pick
    // order, not the library list order.
    fireEvent.click(await screen.findByRole('button', { name: /photo-b\.jpg/ }));
    fireEvent.click(await screen.findByRole('button', { name: /photo-a\.jpg/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Gallery day' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.content_type).toBe('images');
    // Order = pick order (photo-b then photo-a), never the list order.
    expect(arg.resource_ids).toEqual(['img-2', 'img-1']);
    // Images always broadcast — one note per account.
    expect(arg.distribution_mode).toBe('broadcast');
  });

  it('expands a gallery card into its child images in position order', async () => {
    // API returns the children OUT of position order — the picker must sort by
    // position, never trust the response order.
    getGalleryItems.mockResolvedValueOnce([
      { id: 'c-2', filename: 'child-2.jpg', thumbnail_path: null, position: 1 },
      { id: 'c-1', filename: 'child-1.jpg', thumbnail_path: null, position: 0 },
    ]);

    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    // The gallery entity renders as its own card (badge shows the child count).
    fireEvent.click(await screen.findByRole('button', { name: /my-gallery/ }));
    await waitFor(() => expect(getGalleryItems).toHaveBeenCalledWith('gal-1'));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Gallery expand' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.content_type).toBe('images');
    // Children are published in POSITION order (c-1 then c-2), not the API's
    // reversed order — and the gallery entity id itself is never published.
    expect(arg.resource_ids).toEqual(['c-1', 'c-2']);
    expect(arg.resource_ids).not.toContain('gal-1');
  });

  it('removes the whole gallery group on a second pick (toggle)', async () => {
    getGalleryItems.mockResolvedValueOnce([
      { id: 'c-1', filename: 'child-1.jpg', thumbnail_path: null, position: 0 },
      { id: 'c-2', filename: 'child-2.jpg', thumbnail_path: null, position: 1 },
    ]);

    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    fireEvent.click(screen.getByText('HEYGO'));                       // pick account
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Toggle group' },
    });
    const publishBtn = screen.getByRole('button', { name: /Publish now/i });

    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    const galleryCard = await screen.findByRole('button', { name: /my-gallery/ });

    // First pick expands + selects the whole group → publishable.
    fireEvent.click(galleryCard);
    await waitFor(() => expect(getGalleryItems).toHaveBeenCalledWith('gal-1'));
    await waitFor(() => expect(publishBtn).not.toBeDisabled());
    const fetchesAfterExpand = getGalleryItems.mock.calls.length;

    // Second pick removes every child → nothing selected → publish blocked.
    fireEvent.click(galleryCard);
    await waitFor(() => expect(publishBtn).toBeDisabled());
    // Cached children mean the toggle-off never re-fetches.
    expect(getGalleryItems.mock.calls.length).toBe(fetchesAfterExpand);
  });

  it('uploads picked images inline and auto-selects them in pick order', async () => {
    uploadResource.mockReset();
    uploadResource
      .mockResolvedValueOnce({ id: 'up-1', filename: 'up-a.jpg' })
      .mockResolvedValueOnce({ id: 'up-2', filename: 'up-b.jpg' });

    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    // Images mode exposes an enabled Upload button + a hidden file input.
    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    const input = screen.getByLabelText(/Upload images/i);
    const fileA = new File(['a'], 'up-a.jpg', { type: 'image/jpeg' });
    const fileB = new File(['b'], 'up-b.jpg', { type: 'image/jpeg' });
    fireEvent.change(input, { target: { files: [fileA, fileB] } });

    await waitFor(() => expect(uploadResource).toHaveBeenCalledTimes(2));
    // Both uploads auto-selected → their thumbs appear in the content card.
    await waitFor(() => expect(screen.getByLabelText('up-a.jpg')).toBeInTheDocument());
    expect(screen.getByLabelText('up-b.jpg')).toBeInTheDocument();

    fireEvent.click(screen.getByText('HEYGO'));
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Uploaded gallery' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Publish now/i }));

    await waitFor(() => expect(createPublishTask).toHaveBeenCalled());
    const arg = createPublishTask.mock.calls.at(-1)?.[0];
    expect(arg.content_type).toBe('images');
    // Selection order follows the pick order, not upload completion timing.
    expect(arg.resource_ids).toEqual(['up-1', 'up-2']);
  });

  it('shows an error toast when an image upload fails', async () => {
    addToast.mockClear();
    uploadResource.mockReset();
    uploadResource.mockRejectedValueOnce(new Error('boom'));

    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    const input = screen.getByLabelText(/Upload images/i);
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'bad.jpg', { type: 'image/jpeg' })] },
    });

    await waitFor(() => expect(uploadResource).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(expect.stringContaining('failed'), 'error'));
  });

  it('clears the selection when toggling content type', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());

    // Pick a video — the content card shows a thumb labelled with its filename.
    fireEvent.click(screen.getByRole('button', { name: /Add from Library/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clip-a\.mp4/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Done$/i }));
    expect(screen.getByLabelText('clip-a.mp4')).toBeInTheDocument();

    // Flip to Images — the video selection must reset (thumb gone).
    fireEvent.click(screen.getByRole('tab', { name: /^Images$/ }));
    await waitFor(() =>
      expect(screen.queryByLabelText('clip-a.mp4')).toBeNull());
  });
});
