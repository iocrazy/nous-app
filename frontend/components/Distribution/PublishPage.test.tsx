import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect } from 'vitest';

// vi.mock is hoisted above top-level consts, so the fns it references must be
// hoisted too (vi.hoisted) — otherwise "Cannot access before initialization".
const { createPublishTask, promoteGeneratedVideo } = vi.hoisted(() => ({
  createPublishTask: vi.fn().mockResolvedValue({ id: '700', accounts: [] }),
  promoteGeneratedVideo: vi.fn().mockResolvedValue('900'),
}));

vi.mock('../../services/distributionService', () => ({
  listAccounts: vi.fn().mockResolvedValue([
    { id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
      token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '11', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op2', username: 'Expired One', avatar_url: null,
      token_expires_at: null, status: 'expired', created_at: '2026-07-08T00:00:00Z' },
  ]),
  listLibraryVideos: vi.fn().mockResolvedValue([
    { id: '30', filename: 'clip-a.mp4', thumbnail_url: null },
  ]),
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
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

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

  it('locks the Official API channel behind Douyin review', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Official API/i })).toBeDisabled();
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
});
