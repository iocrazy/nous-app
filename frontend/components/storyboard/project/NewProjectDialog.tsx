import { useState } from 'react';

interface NewProjectDialogProps {
  onCreate: (name: string) => void;
  onCancel: () => void;
}

export default function NewProjectDialog({ onCreate, onCancel }: NewProjectDialogProps) {
  const [name, setName] = useState('');

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-sm rounded-lg border border-zinc-700 bg-zinc-900 p-6">
        <h3 className="mb-4 text-sm font-medium text-zinc-200">New Project</h3>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Project name"
          autoFocus
          className="mb-4 w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-indigo-500 focus:outline-none"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && name.trim()) onCreate(name.trim());
            if (e.key === 'Escape') onCancel();
          }}
        />
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => name.trim() && onCreate(name.trim())}
            disabled={!name.trim()}
            className="rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            Create
          </button>
        </div>
      </div>
    </div>
  );
}
