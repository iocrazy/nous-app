// components/Distribution/CoverStudio/AddCoverTemplateModal.tsx
//
// The two ways a picture becomes a cover template: upload a file, or pick one
// already in your library. Both were asked for, and they are one modal rather
// than two entry points because from the user's side it is a single decision
// ("which picture?") that happens to have two sources.
//
// Both paths end in the same place — the cover-template folder in the
// resource library. An upload lands there directly; a library pick is LINKED
// into it (the picture stays wherever else it already lives). There is no
// separate name to type: the picture's filename is its name, as in the
// library. coverTemplateService owns the calls; this file only collects the
// picture.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ImagePlus, Upload } from 'lucide-react';

import { UiButton, UiModal } from '../../ui';
import { useComposerDropzone } from '../../../hooks/useComposerDropzone';
import { listLibraryMediaOrThrow } from '../../../services/distributionService';
import {
  addCoverTemplateFromFile,
  addCoverTemplateFromResource,
  type CoverTemplate,
} from '../../../services/coverTemplateService';
import type { LibraryVideo } from '../../../types';
import './cover-studio.css';

// A gallery is a container of images, not an image. Handing one to the model
// as a single reference is meaningless, and `listLibraryMedia` includes them
// in image mode on purpose for the publish picker, which CAN post a gallery.
const GALLERY_MIME = 'application/x-mediahub-gallery';

interface Props {
  open: boolean;
  scopeId: string;
  onClose: () => void;
  onAdded: (template: CoverTemplate) => void;
}

type Picked =
  | { kind: 'file'; file: File; previewUrl: string }
  | { kind: 'resource'; resource: LibraryVideo };

export function AddCoverTemplateModal({
  open,
  scopeId,
  onClose,
  onAdded,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [library, setLibrary] = useState<LibraryVideo[]>([]);
  const [loading, setLoading] = useState(false);
  // Distinct from `library.length === 0`: a failed load must not be rendered
  // as "you own no images", which is a claim we cannot make when the request
  // never came back.
  const [loadError, setLoadError] = useState(false);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const loadLibrary = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const rows = await listLibraryMediaOrThrow(scopeId, { mediaType: 'image' });
      setLibrary(rows.filter((r) => r.mime_type !== GALLERY_MIME));
    } catch (err) {
      console.error('[AddCoverTemplateModal] library load failed:', err);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [scopeId]);

  useEffect(() => {
    if (!open) return;
    void loadLibrary();
  }, [open, loadLibrary]);

  // Reset on close so reopening never shows the previous attempt's selection
  // or error, and revoke the object URL so a long session does not leak one
  // blob per preview.
  useEffect(() => {
    if (open) return;
    setPicked((prev) => {
      if (prev?.kind === 'file') URL.revokeObjectURL(prev.previewUrl);
      return null;
    });
    setSaveError(null);
    setSaving(false);
  }, [open]);

  const pickFile = useCallback((file: File) => {
    setSaveError(null);
    setPicked((prev) => {
      if (prev?.kind === 'file') URL.revokeObjectURL(prev.previewUrl);
      return { kind: 'file', file, previewUrl: URL.createObjectURL(file) };
    });
  }, []);

  const onFiles = useCallback(
    (files: FileList) => {
      const file = Array.from(files).find((f) =>
        (f.type || '').toLowerCase().startsWith('image/'),
      );
      if (!file) {
        setSaveError(
          t(
            'distribution.coverStudio.templateNeedsImage',
            'A cover template has to be an image.',
          ),
        );
        return;
      }
      pickFile(file);
    },
    [pickFile, t],
  );

  const { rootProps, isDragActive } = useComposerDropzone({ onFiles });

  const save = useCallback(async () => {
    if (!picked || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const created =
        picked.kind === 'file'
          ? await addCoverTemplateFromFile(picked.file, scopeId)
          : await addCoverTemplateFromResource(picked.resource.id, scopeId);
      onAdded(created);
      onClose();
    } catch (err) {
      console.error('[AddCoverTemplateModal] save failed:', err);
      const failure = (err as { failure?: string })?.failure;
      setSaveError(
        failure === 'not-an-image'
          ? t(
              'distribution.coverStudio.templateNeedsImage',
              'A cover template has to be an image.',
            )
          : t(
              'distribution.coverStudio.templateSaveFailed',
              'That picture could not be saved as a template. Try again.',
            ),
      );
    } finally {
      setSaving(false);
    }
  }, [picked, saving, scopeId, onAdded, onClose, t]);

  return (
    <UiModal
      isOpen={open}
      title={t('distribution.coverStudio.addTemplate', 'Add a template')}
      onClose={onClose}
      widthClassName="w-[520px] cs-light-panel"
      containerClassName="cover-studio-modal"
      footer={
        <>
          <UiButton variant="ghost" onClick={onClose}>
            {t('common.cancel', 'Cancel')}
          </UiButton>
          <UiButton
            variant="primary"
            disabled={!picked || saving}
            onClick={() => void save()}
            data-testid="cover-template-save"
          >
            {saving
              ? t('distribution.coverStudio.saving', 'Saving…')
              : t('distribution.coverStudio.saveTemplate', 'Save template')}
          </UiButton>
        </>
      }
    >
      <p className="cs-hint">
        {t(
          'distribution.coverStudio.templateGoesToFolder',
          'It goes into your cover-template folder in the library. Pick from the library and the picture is linked there, not copied.',
        )}
      </p>

      <div
        {...rootProps}
        className={`cs-drop ${isDragActive ? 'over' : ''}`}
        style={{ marginTop: 12 }}
        onClick={() => fileInputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click();
        }}
        data-testid="cover-template-dropzone"
      >
        <Upload size={16} />
        <div>
          {picked?.kind === 'file'
            ? picked.file.name
            : t(
                'distribution.coverStudio.dropOrBrowse',
                'Drop a picture here, or click to choose a file',
              )}
        </div>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        hidden
        onChange={(e) => {
          if (e.target.files?.length) onFiles(e.target.files);
          // Clear so choosing the SAME file twice in a row still fires change.
          e.target.value = '';
        }}
      />

      <div className="cs-sep">
        {t('distribution.coverStudio.orFromLibrary', 'or pick from your library')}
      </div>

      {loadError ? (
        <div className="cs-error">
          {t(
            'distribution.coverStudio.libraryLoadFailed',
            'Could not load your library.',
          )}
          <button type="button" onClick={() => void loadLibrary()}>
            {t('common.retry', 'Retry')}
          </button>
        </div>
      ) : loading ? (
        <div className="cs-empty">{t('common.loading', 'Loading…')}</div>
      ) : library.length === 0 ? (
        <div className="cs-empty">
          {t(
            'distribution.coverStudio.libraryNoImages',
            'No images in your library yet — upload one above.',
          )}
        </div>
      ) : (
        <div className="cs-lib" data-testid="cover-template-library">
          {library.map((row) => {
            const on = picked?.kind === 'resource' && picked.resource.id === row.id;
            return (
              <button
                key={row.id}
                type="button"
                className={on ? 'on' : ''}
                title={row.filename}
                aria-pressed={on}
                onClick={() => {
                  setSaveError(null);
                  setPicked((prev) => {
                    if (prev?.kind === 'file') URL.revokeObjectURL(prev.previewUrl);
                    return { kind: 'resource', resource: row };
                  });
                }}
              >
                {row.thumbnail_url ? (
                  <img src={row.thumbnail_url} alt={row.filename} />
                ) : (
                  <span className="noimg">
                    <ImagePlus size={14} />
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {saveError && (
        <div className="cs-error" style={{ margin: '12px 0 0' }}>
          {saveError}
        </div>
      )}
    </UiModal>
  );
}
