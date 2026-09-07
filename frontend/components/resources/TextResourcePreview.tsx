// frontend/components/resources/TextResourcePreview.tsx
// Dispatcher for text-type resources on the detail page (spec 2026-07-14):
// fetch the raw text, classify it, render the right TipTap editor read-only,
// and (for the creator) offer edit + save-as-new-version / overwrite.
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, Pencil, Save, X } from 'lucide-react';
import type { Resource } from '../../types';
import { classifyTextResource } from '../../utils/textResourceMode';
import {
  saveTextAsNewVersion,
  overwriteVersionContent,
  fetchResourceVersions,
} from '../../services/resourceService';
import { MarkdownResourceEditor } from './MarkdownResourceEditor';
import { PlainTextResourceEditor } from './PlainTextResourceEditor';
import { useToast } from '../Toast';

interface Props {
  resource: Resource;
  fileUrl: string;
  canEdit: boolean;
  onSaved?: () => void;
}

function extOf(filename: string | null): string {
  if (!filename) return '';
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot + 1).toLowerCase() : '';
}

export function TextResourcePreview({ resource, fileUrl, canEdit, onSaved }: Props) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [text, setText] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mode = useMemo(
    () =>
      classifyTextResource({
        filename: resource.filename,
        mime: resource.mime_type,
        sizeBytes: resource.file_size_bytes ?? null,
      }),
    [resource.filename, resource.mime_type, resource.file_size_bytes],
  );
  const ext = extOf(resource.filename);

  useEffect(() => {
    let alive = true;
    // Oversize files never fetch the full body — the read-only fallback shows
    // a truncated slice; but we still fetch (Range) a 64 KB head. For v1 we
    // fetch the whole body only for editable sizes; oversize shows download.
    if (mode === 'oversize' || mode === null) {
      setText('');
      return;
    }
    (async () => {
      try {
        const resp = await fetch(fileUrl);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const body = await resp.text();
        if (alive) {
          setText(body);
          setDraft(body);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [fileUrl, mode]);

  if (mode === 'oversize') {
    return (
      <div className="flex flex-col items-center gap-4 text-center">
        <p className="text-content-2 font-medium">{resource.filename}</p>
        <p className="text-content-3 text-sm">
          {t('resources.textTooLargeToEdit', 'File is too large to preview or edit inline.')}
        </p>
        <a
          href={fileUrl}
          download
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm"
        >
          <Download size={16} />
          {t('resources.download', 'Download')}
        </a>
      </div>
    );
  }

  if (error) {
    return <p className="text-content-3 text-sm">{t('resources.previewFailed', 'Failed to load preview')}: {error}</p>;
  }
  if (text === null) {
    return <p className="text-content-3 text-sm">{t('common.loading', 'Loading...')}</p>;
  }

  const Editor =
    mode === 'markdown' ? MarkdownResourceEditor : PlainTextResourceEditor;

  const runSave = async (kind: 'new' | 'overwrite') => {
    setSaving(true);
    try {
      const filename = resource.filename || 'file.txt';
      const mime = resource.mime_type || 'text/plain';
      if (kind === 'new') {
        await saveTextAsNewVersion(String(resource.id), draft, filename, mime);
      } else {
        const versions = await fetchResourceVersions(String(resource.id));
        const current =
          versions.find((v) => v.version_number === resource.current_version) ??
          versions[0];
        if (!current) throw new Error('no current version');
        await overwriteVersionContent(
          String(resource.id),
          String(current.id),
          draft,
          filename,
          mime,
        );
      }
      addToast(t('resources.saved', 'Saved'), 'success');
      setText(draft);
      setEditing(false);
      onSaved?.();
    } catch (e) {
      console.error('Failed to save text resource:', e);
      // The version writers throw the backend's own sentence when it sent
      // one (e.g. the object-store 503) — say that rather than a bare
      // "Failed to save".
      const reason = e instanceof Error && e.message ? e.message : '';
      addToast(reason || t('resources.saveFailed', 'Failed to save'), 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="w-full">
      <div className="flex items-center justify-end gap-2 mb-2">
        {!editing && canEdit && (
          <button
            type="button"
            onClick={() => {
              setDraft(text);
              setEditing(true);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-island-2 text-content-2 text-xs hover:bg-line"
          >
            <Pencil size={14} />
            {t('resources.edit', 'Edit')}
          </button>
        )}
        {editing && (
          <>
            <button
              type="button"
              disabled={saving}
              onClick={() => runSave('new')}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs disabled:opacity-60"
            >
              <Save size={14} />
              {t('resources.saveAsNewVersion', 'Save as new version')}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => runSave('overwrite')}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-island-2 text-content-2 text-xs hover:bg-line disabled:opacity-60"
            >
              {t('resources.overwriteCurrentVersion', 'Overwrite current version')}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => {
                setDraft(text);
                setEditing(false);
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-content-3 text-xs hover:bg-line"
            >
              <X size={14} />
              {t('common.cancel', 'Cancel')}
            </button>
          </>
        )}
      </div>
      <Editor
        key={`${resource.id}-${editing}`}
        value={editing ? draft : text}
        ext={ext}
        readOnly={!editing}
        onChange={setDraft}
      />
    </div>
  );
}

export default TextResourcePreview;
