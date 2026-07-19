import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { GalleryUploadDialog } from './GalleryUploadDialog';
import {
  createGallery,
  setGalleryItems,
  uploadResource,
} from '../services/resourceService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
}));
vi.mock('../services/resourceService', () => ({
  createGallery: vi.fn(),
  setGalleryItems: vi.fn(),
  uploadResource: vi.fn(),
}));

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function makeFiles(): File[] {
  return [
    new File(['a'], 'a.jpg', { type: 'image/jpeg' }),
    new File(['b'], 'b.jpg', { type: 'image/jpeg' }),
  ];
}

describe('GalleryUploadDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    asMock(createGallery).mockResolvedValue({ id: 'gal-1' });
    // Return the file name as the resource id so we can assert ORDER.
    asMock(uploadResource).mockImplementation(async (file: File) => ({ id: file.name }));
    asMock(setGalleryItems).mockResolvedValue([]);
  });

  it('creates gallery, uploads each image, then attaches ordered children', async () => {
    const onCreated = vi.fn();
    const addToast = vi.fn();
    render(
      <GalleryUploadDialog
        isOpen
        onClose={() => {}}
        scopeId="scope-1"
        folderId="folder-9"
        addToast={addToast}
        onCreated={onCreated}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('Enter gallery name'), {
      target: { value: 'My Trip' },
    });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: makeFiles() } });

    fireEvent.click(screen.getByRole('button', { name: /create gallery/i }));

    await waitFor(() => expect(setGalleryItems).toHaveBeenCalled());

    // Step 1: gallery created with name + scope + folder.
    expect(createGallery).toHaveBeenCalledWith('scope-1', 'My Trip', 'folder-9');
    // Step 2: every image uploaded into the scope.
    expect(uploadResource).toHaveBeenCalledTimes(2);
    // Step 3: children attached in the SELECTED order (file names as ids).
    expect(setGalleryItems).toHaveBeenCalledWith('gal-1', 'scope-1', ['a.jpg', 'b.jpg']);
    expect(onCreated).toHaveBeenCalled();
    expect(addToast).toHaveBeenCalledWith('Gallery created', 'success');
  });

  it('does not submit without a name or images', () => {
    render(
      <GalleryUploadDialog
        isOpen
        onClose={() => {}}
        scopeId="scope-1"
        folderId={null}
        addToast={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    // Button disabled until both name + files are present.
    const btn = screen.getByRole('button', { name: /create gallery/i });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(createGallery).not.toHaveBeenCalled();
  });

  it('reports a partial failure when some uploads fail', async () => {
    asMock(uploadResource).mockImplementation(async (file: File) => {
      if (file.name === 'b.jpg') throw new Error('boom');
      return { id: file.name };
    });
    const addToast = vi.fn();
    render(
      <GalleryUploadDialog
        isOpen
        onClose={() => {}}
        scopeId="scope-1"
        folderId={null}
        addToast={addToast}
        onCreated={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText('Enter gallery name'), {
      target: { value: 'Partial' },
    });
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, {
      target: { files: makeFiles() },
    });
    fireEvent.click(screen.getByRole('button', { name: /create gallery/i }));

    await waitFor(() => expect(setGalleryItems).toHaveBeenCalled());
    // Only the successful image is attached.
    expect(setGalleryItems).toHaveBeenCalledWith('gal-1', 'scope-1', ['a.jpg']);
    expect(addToast).toHaveBeenCalledWith(expect.any(String), 'info');
  });
});
