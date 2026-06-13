import { useState } from 'react';

interface NewProjectDialogProps {
  onCreate: (name: string) => void;
  onCancel: () => void;
}

export default function NewProjectDialog({ onCreate, onCancel }: NewProjectDialogProps) {
  const [name, setName] = useState('');

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-sm rounded-xl border border-ink-700 bg-ink-900 p-6">
        <h3 className="mb-4 text-sm font-semibold text-ink-100">New Project</h3>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Project name"
          autoFocus
          className="mb-4 w-full rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-ink-100 placeholder-ink-500 focus:border-indigo-500 focus:outline-none transition-colors"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && name.trim()) onCreate(name.trim());
            if (e.key === 'Escape') onCancel();
          }}
        />
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-1.5 text-xs text-ink-200 transition-colors hover:bg-ink-700"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => name.trim() && onCreate(name.trim())}
            disabled={!name.trim()}
            className="rounded-lg bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
          >
            Create
          </button>
        </div>
      </div>
    </div>
  );
}
