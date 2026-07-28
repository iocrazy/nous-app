import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// i18n: return the default string (or key) — no provider needed.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
}));

const { getGalleryItems } = vi.hoisted(() => ({ getGalleryItems: vi.fn() }));

vi.mock('../../services/resourceService', () => ({
  getGalleryItems,
  // Encode the child id into the URL so the test can read which image is shown.
  getResourceMediaUrl: (id: string, token?: string) => `/media/${id}?token=${token ?? ''}`,
  GALLERY_MIME: 'application/x-mediahub-gallery',
}));

// ResourcePromptSection does its own supabase/resourceService data fetching
// (covered by its own test suite) — stub it here so the prompt-drawer tests
// below only assert GalleryViewer's mount gating (lazy, keyed to `current.id`)
// without pulling in a real Supabase client.
let capturedPromptResourceId: string | null = null;
vi.mock('./ResourcePromptSection', () => ({
  ResourcePromptSection: ({ resourceId }: { resourceId: string }) => {
    capturedPromptResourceId = resourceId;
    return <div data-testid="resource-prompt-section">{resourceId}</div>;
  },
}));

import { GalleryViewer } from './GalleryViewer';

const currentSrc = () => (screen.getByRole('img') as HTMLImageElement).getAttribute('src');

describe('GalleryViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedPromptResourceId = null;
  });

  it('renders the 1/n pager in position order and steps with arrows + keys', async () => {
    // API returns children OUT of order — the viewer must sort by position so
    // the first frame is position 0 (child-1), not the API's first row.
    getGalleryItems.mockResolvedValue([
      { id: 'child-2', filename: 'b.jpg', thumbnail_path: null, position: 1 },
      { id: 'child-1', filename: 'a.jpg', thumbnail_path: null, position: 0 },
    ]);

    render(<GalleryViewer galleryId="gal-1" mediaToken="tok" />);

    // First frame = position 0, counter reads 1 / 2.
    await waitFor(() => expect(screen.getByText('1 / 2')).toBeInTheDocument());
    expect(getGalleryItems).toHaveBeenCalledWith('gal-1');
    expect(currentSrc()).toBe('/media/child-1?token=tok');

    // Next arrow → second frame.
    fireEvent.click(screen.getByRole('button', { name: /Next image/i }));
    expect(screen.getByText('2 / 2')).toBeInTheDocument();
    expect(currentSrc()).toBe('/media/child-2?token=tok');

    // Keyboard ArrowLeft → back to the first frame.
    fireEvent.keyDown(window, { key: 'ArrowLeft' });
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    expect(currentSrc()).toBe('/media/child-1?token=tok');
  });

  it('shows a friendly empty state for a gallery with no images', async () => {
    getGalleryItems.mockResolvedValue([]);
    render(<GalleryViewer galleryId="gal-empty" />);
    await waitFor(() =>
      expect(screen.getByText(/no images/i)).toBeInTheDocument());
    expect(screen.queryByRole('img')).toBeNull();
  });

  describe('per-child prompt drawer', () => {
    beforeEach(() => {
      getGalleryItems.mockResolvedValue([
        { id: 'child-1', filename: 'a.jpg', thumbnail_path: null, position: 0 },
        { id: 'child-2', filename: 'b.jpg', thumbnail_path: null, position: 1 },
      ]);
    });

    it('stays collapsed by default — ResourcePromptSection is not mounted until the toggle is clicked', async () => {
      render(<GalleryViewer galleryId="gal-1" mediaToken="tok" />);
      await waitFor(() => expect(screen.getByText('1 / 2')).toBeInTheDocument());

      expect(screen.queryByTestId('resource-prompt-section')).toBeNull();
      expect(capturedPromptResourceId).toBeNull();
    });

    it('mounts ResourcePromptSection for the CURRENT child when the Prompt toggle is opened', async () => {
      render(<GalleryViewer galleryId="gal-1" mediaToken="tok" />);
      await waitFor(() => expect(screen.getByText('1 / 2')).toBeInTheDocument());

      fireEvent.click(screen.getByText('Prompt'));

      expect(screen.getByTestId('resource-prompt-section')).toHaveTextContent('child-1');
      expect(capturedPromptResourceId).toBe('child-1');
    });

    it('closes the drawer and re-mounts for the new child on navigation', async () => {
      render(<GalleryViewer galleryId="gal-1" mediaToken="tok" />);
      await waitFor(() => expect(screen.getByText('1 / 2')).toBeInTheDocument());

      fireEvent.click(screen.getByText('Prompt'));
      expect(screen.getByTestId('resource-prompt-section')).toHaveTextContent('child-1');

      fireEvent.click(screen.getByRole('button', { name: /Next image/i }));
      expect(screen.getByText('2 / 2')).toBeInTheDocument();

      // Drawer collapses on navigation — ResourcePromptSection for child-1 unmounts.
      expect(screen.queryByTestId('resource-prompt-section')).toBeNull();

      fireEvent.click(screen.getByText('Prompt'));
      expect(screen.getByTestId('resource-prompt-section')).toHaveTextContent('child-2');
    });

    it('closes via the drawer\'s own close button, restoring the corner toggle pill', async () => {
      render(<GalleryViewer galleryId="gal-1" mediaToken="tok" />);
      await waitFor(() => expect(screen.getByText('1 / 2')).toBeInTheDocument());

      fireEvent.click(screen.getByText('Prompt'));
      expect(screen.getByTestId('resource-prompt-section')).toBeInTheDocument();

      fireEvent.click(screen.getByLabelText('Close'));

      expect(screen.queryByTestId('resource-prompt-section')).toBeNull();
      expect(screen.getByText('Prompt')).toBeInTheDocument();
    });
  });
});
