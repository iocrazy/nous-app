/**
 * TempResourceActions — Save / "Save to folder" split-button for temp-folder rows.
 *
 * - "Save"            → promoteResource(id, {folderId: null, ...scope})  (move to scope root)
 * - "Save to folder…" → opens FolderPickerModal then promoteResource(id, {folderId: picked, ...scope})
 *
 * Both call back via `onDone` so the caller can refresh the row list.
 */

import { useState } from 'react';
import { tempTtlService, ScopeType } from '../services/tempTtlService';
import { FolderPickerModal } from './FolderPickerModal';

interface Props {
  resourceId: string;
  scopeType: ScopeType;
  scopeId: string;
  onDone: () => void;
}

export function TempResourceActions({ resourceId, scopeType, scopeId, onDone }: Props) {
  const [busy, setBusy] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function promote(folderId: string | null) {
    setBusy(true);
    setError(null);
    try {
      await tempTtlService.promoteResource(resourceId, {
        folderId,
        scopeType,
        scopeId,
      });
      onDone();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inline-flex items-center gap-1">
      <button
        type="button"
        disabled={busy}
        className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
        onClick={() => promote(null)}
      >
        Save
      </button>
      <button
        type="button"
        disabled={busy}
        aria-label="Save to folder"
        className="rounded bg-blue-600 px-1 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
        onClick={() => setPickerOpen(true)}
      >
        ▾
      </button>
      <FolderPickerModal
        isOpen={pickerOpen}
        mode="move"
        isPersonal={scopeType === 'personal'}
        scopeId={scopeId}
        onClose={() => setPickerOpen(false)}
        onConfirm={(folderId) => {
          setPickerOpen(false);
          promote(folderId);
        }}
      />
      {error && <span className="text-xs text-red-600 dark:text-red-400">{error}</span>}
    </div>
  );
}
