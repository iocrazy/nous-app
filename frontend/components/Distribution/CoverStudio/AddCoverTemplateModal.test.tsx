/**
 * The add-template modal — the five properties worth pinning.
 *
 *   1. Upload and library-pick reach DIFFERENT service calls; the library path
 *      also records which resource it came from (provenance).
 *   2. A gallery entity is filtered out. `listLibraryMedia` includes galleries
 *      in image mode on purpose (the publish picker CAN post one), but a
 *      gallery is a container of images, not an image — handing one to the
 *      model as a single reference is meaningless.
 *   3. "could not load your library" and "no images yet" are different boxes.
 *   4. A non-image drop is refused BEFORE any upload happens.
 *   5. The name is trimmed, and a blank one cannot be saved — the server
 *      rejects it too, but a disabled button beats a round-trip to a 422.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import enJson from '../../../public/locales/en.json';

const { listLibraryMediaOrThrow } = vi.hoisted(() => ({
  listLibraryMediaOrThrow: vi.fn(),
}));
vi.mock('../../../services/distributionService', () => ({ listLibraryMediaOrThrow }));

const { addCoverTemplateFromFile, addCoverTemplateFromResource } = vi.hoisted(() => ({
  addCoverTemplateFromFile: vi.fn(),
  addCoverTemplateFromResource: vi.fn(),
}));
vi.mock('../../../services/coverTemplateService', () => ({
  addCoverTemplateFromFile,
  addCoverTemplateFromResource,
}));

import { AddCoverTemplateModal } from './AddCoverTemplateModal';

const IMAGE_ROW = {
  id: '777',
  filename: 'reference.png',
  thumbnail_url: 'https://api.test/api/v1/resources/777/cover',
  mime_type: 'image/png',
};
const GALLERY_ROW = {
  id: '888',
  filename: 'My album',
  thumbnail_url: null,
  mime_type: 'application/x-mediahub-gallery',
  gallery_count: 9,
};

const SAVED = {
  id: '341588599799820',
  name: 'reference',
  generated_media_id: '341582104263581',
  image_url: '/api/v1/generated-media/341582104263581/cover',
  source_kind: 'upload' as const,
  source_resource_id: null,
  usage_count: 0,
  last_used_at: null,
  created_at: '2026-08-22T00:00:00Z',
};

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

function renderModal(over: Partial<React.ComponentProps<typeof AddCoverTemplateModal>> = {}) {
  const onAdded = vi.fn();
  const onClose = vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <AddCoverTemplateModal
        open
        scopeId="42"
        onClose={onClose}
        onAdded={onAdded}
        {...over}
      />
    </I18nextProvider>,
  );
  return { onAdded, onClose };
}

/** jsdom has no createObjectURL; the component uses it for the file preview. */
beforeEach(() => {
  vi.clearAllMocks();
  listLibraryMediaOrThrow.mockResolvedValue([IMAGE_ROW, GALLERY_ROW]);
  addCoverTemplateFromFile.mockResolvedValue(SAVED);
  addCoverTemplateFromResource.mockResolvedValue({ ...SAVED, source_kind: 'library' });
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:x'), writable: true });
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), writable: true });
});

function dropFile(file: File) {
  const zone = screen.getByTestId('cover-template-dropzone');
  fireEvent.drop(zone, { dataTransfer: { files: [file], types: ['Files'] } });
}

describe('AddCoverTemplateModal', () => {
  it('leaves a gallery entity out of the library grid', async () => {
    renderModal();

    const grid = await screen.findByTestId('cover-template-library');
    // Positive proof the grid rendered, so "the gallery is absent" cannot pass
    // just because nothing rendered at all.
    expect(screen.getByTitle('reference.png')).toBeTruthy();
    expect(grid.querySelectorAll('button')).toHaveLength(1);
    expect(screen.queryByTitle('My album')).toBeNull();
  });

  it('uploading a file saves it as a template under the typed name', async () => {
    const { onAdded, onClose } = renderModal();
    await screen.findByTestId('cover-template-library');

    dropFile(new File(['x'], 'bold-headline.png', { type: 'image/png' }));

    // The name seeds from the filename, extension stripped.
    const input = (await screen.findByLabelText('Name')) as HTMLInputElement;
    expect(input.value).toBe('bold-headline');

    fireEvent.change(input, { target: { value: '  Bold headline  ' } });
    fireEvent.click(screen.getByTestId('cover-template-save'));

    await waitFor(() =>
      expect(addCoverTemplateFromFile).toHaveBeenCalledWith(
        expect.any(File),
        'Bold headline',
      ),
    );
    expect(addCoverTemplateFromResource).not.toHaveBeenCalled();
    await waitFor(() => expect(onAdded).toHaveBeenCalledWith(SAVED));
    expect(onClose).toHaveBeenCalled();
  });

  it('picking from the library records the resource it came from', async () => {
    renderModal();
    await screen.findByTestId('cover-template-library');

    fireEvent.click(screen.getByTitle('reference.png'));
    fireEvent.click(screen.getByTestId('cover-template-save'));

    await waitFor(() =>
      expect(addCoverTemplateFromResource).toHaveBeenCalledWith('777', 'reference'),
    );
    expect(addCoverTemplateFromFile).not.toHaveBeenCalled();
  });

  it('refuses a non-image before anything is uploaded', async () => {
    renderModal();
    await screen.findByTestId('cover-template-library');

    dropFile(new File(['x'], 'clip.mp4', { type: 'video/mp4' }));

    expect(await screen.findByText(/has to be an image/)).toBeTruthy();
    expect(addCoverTemplateFromFile).not.toHaveBeenCalled();
    // No name field either — nothing was picked.
    expect(screen.queryByLabelText('Name')).toBeNull();
  });

  it('says the library FAILED rather than showing it as empty', async () => {
    listLibraryMediaOrThrow.mockRejectedValueOnce(new Error('boom'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderModal();

    expect(await screen.findByText(/Could not load your library/)).toBeTruthy();
    expect(screen.queryByText(/No images in your library yet/)).toBeNull();
    spy.mockRestore();
  });

  it('says "no images yet" only when the load actually succeeded and was empty', async () => {
    listLibraryMediaOrThrow.mockResolvedValueOnce([]);

    renderModal();

    expect(await screen.findByText(/No images in your library yet/)).toBeTruthy();
    expect(screen.queryByText(/Could not load your library/)).toBeNull();
  });

  it('cannot save with a blank name', async () => {
    renderModal();
    await screen.findByTestId('cover-template-library');
    dropFile(new File(['x'], 'ref.png', { type: 'image/png' }));

    const input = (await screen.findByLabelText('Name')) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '   ' } });

    expect(
      (screen.getByTestId('cover-template-save') as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('reports a save failure instead of closing silently', async () => {
    addCoverTemplateFromFile.mockRejectedValueOnce(
      Object.assign(new Error('nope'), { failure: 'server' }),
    );
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { onClose } = renderModal();
    await screen.findByTestId('cover-template-library');
    dropFile(new File(['x'], 'ref.png', { type: 'image/png' }));
    await screen.findByLabelText('Name');

    fireEvent.click(screen.getByTestId('cover-template-save'));

    expect(await screen.findByText(/could not be saved as a template/)).toBeTruthy();
    // Closing on failure would look exactly like success.
    expect(onClose).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
