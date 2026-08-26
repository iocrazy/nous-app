/**
 * The add-template modal — the properties worth pinning.
 *
 *   1. Upload and library-pick reach DIFFERENT service calls, and both carry
 *      the scope, because both end in the same scope's template folder.
 *   2. A gallery entity is filtered out. `listLibraryMedia` includes galleries
 *      in image mode on purpose (the publish picker CAN post one), but a
 *      gallery is a container of images, not an image — handing one to the
 *      model as a single reference is meaningless.
 *   3. "could not load your library" and "no images yet" are different boxes.
 *   4. A non-image drop is refused BEFORE any upload happens.
 *   5. Nothing can be saved until a picture is picked; there is no name to
 *      type — the filename is the name, as in the library.
 *   6. A failed save keeps the modal open and says so.
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
  resource_id: '777',
  name: 'reference.png',
  mime_type: 'image/png',
  thumb_url: '/api/v1/resources/777/cover',
  usage_count: 0,
  last_used_at: null,
};

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return inst;
};

function renderModal(over: Partial<React.ComponentProps<typeof AddCoverTemplateModal>> = {}) {
  const onAdded = vi.fn();
  const onClose = vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <AddCoverTemplateModal open scopeId="scope-1" onClose={onClose} onAdded={onAdded} {...over} />
    </I18nextProvider>,
  );
  return { onAdded, onClose };
}

function dropFile(file: File) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

beforeEach(() => {
  vi.clearAllMocks();
  listLibraryMediaOrThrow.mockResolvedValue([IMAGE_ROW, GALLERY_ROW]);
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:preview'),
    revokeObjectURL: vi.fn(),
  });
});

describe('AddCoverTemplateModal', () => {
  it('lists library images but NOT galleries', async () => {
    renderModal();
    expect(await screen.findByTitle('reference.png')).toBeTruthy();
    expect(screen.queryByTitle('My album')).toBeNull();
  });

  it('cannot save until a picture is picked, and has no name field', async () => {
    renderModal();
    await screen.findByTitle('reference.png');
    expect((screen.getByTestId('cover-template-save') as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByLabelText('Name')).toBeNull();
  });

  it('library pick → linked into the folder for this scope', async () => {
    addCoverTemplateFromResource.mockResolvedValueOnce(SAVED);
    const { onAdded, onClose } = renderModal();

    fireEvent.click(await screen.findByTitle('reference.png'));
    fireEvent.click(screen.getByTestId('cover-template-save'));

    await waitFor(() => expect(addCoverTemplateFromResource).toHaveBeenCalledWith('777', 'scope-1'));
    expect(addCoverTemplateFromFile).not.toHaveBeenCalled();
    expect(onAdded).toHaveBeenCalledWith(SAVED);
    expect(onClose).toHaveBeenCalled();
  });

  it('upload → the file goes to the folder for this scope', async () => {
    addCoverTemplateFromFile.mockResolvedValueOnce({ ...SAVED, name: 'mine.png' });
    const { onAdded } = renderModal();
    await screen.findByTitle('reference.png');

    const file = new File(['x'], 'mine.png', { type: 'image/png' });
    dropFile(file);
    expect(screen.getByText('mine.png')).toBeTruthy();
    fireEvent.click(screen.getByTestId('cover-template-save'));

    await waitFor(() => expect(addCoverTemplateFromFile).toHaveBeenCalledWith(file, 'scope-1'));
    expect(addCoverTemplateFromResource).not.toHaveBeenCalled();
    expect(onAdded).toHaveBeenCalled();
  });

  it('refuses a non-image before any upload', async () => {
    renderModal();
    await screen.findByTitle('reference.png');

    dropFile(new File(['x'], 'clip.mp4', { type: 'video/mp4' }));

    expect(screen.getByText('A cover template has to be an image.')).toBeTruthy();
    expect((screen.getByTestId('cover-template-save') as HTMLButtonElement).disabled).toBe(true);
    expect(addCoverTemplateFromFile).not.toHaveBeenCalled();
  });

  it('a failed save stays open and says so', async () => {
    addCoverTemplateFromResource.mockRejectedValueOnce(new Error('500'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { onClose } = renderModal();

    fireEvent.click(await screen.findByTitle('reference.png'));
    fireEvent.click(screen.getByTestId('cover-template-save'));

    expect(
      await screen.findByText('That picture could not be saved as a template. Try again.'),
    ).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it('"could not load" and "no images" are different boxes', async () => {
    listLibraryMediaOrThrow.mockRejectedValueOnce(new Error('503'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderModal();
    expect(await screen.findByText('Could not load your library.')).toBeTruthy();
    expect(screen.queryByText(/No images in your library yet/)).toBeNull();

    listLibraryMediaOrThrow.mockResolvedValueOnce([GALLERY_ROW]);
    fireEvent.click(screen.getByText('Retry'));
    expect(await screen.findByText(/No images in your library yet/)).toBeTruthy();
    spy.mockRestore();
  });
});
