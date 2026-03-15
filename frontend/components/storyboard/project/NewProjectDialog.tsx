import React, { useState, useCallback, useRef } from 'react';
import { X, Plus, FileText, Video, Loader2 } from 'lucide-react';
import ScriptImportDialog from './ScriptImportDialog';

// ─── Props ────────────────────────────────────────────────────────────────────

type ProjectSource = 'blank' | 'script' | 'video';

interface NewProjectDialogProps {
  onCreate: (name: string, source: ProjectSource, scriptData?: unknown) => void;
  onCancel: () => void;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const SOURCE_OPTIONS: {
  id: ProjectSource;
  label: string;
  description: string;
  icon: React.ReactNode;
}[] = [
  {
    id: 'blank',
    label: 'Blank Project',
    description: 'Start from scratch on an empty canvas',
    icon: <Plus size={24} />,
  },
  {
    id: 'script',
    label: 'From Script',
    description: 'Import a script and AI generates frames',
    icon: <FileText size={24} />,
  },
  {
    id: 'video',
    label: 'From Video',
    description: 'Extract frames from an existing video',
    icon: <Video size={24} />,
  },
];

// ─── Component ────────────────────────────────────────────────────────────────

const NewProjectDialog = React.memo(function NewProjectDialog({
  onCreate,
  onCancel,
}: NewProjectDialogProps) {
  const [name, setName] = useState('');
  const [source, setSource] = useState<ProjectSource>('blank');
  const [showScriptImport, setShowScriptImport] = useState(false);
  const [creating, setCreating] = useState(false);

  const videoInputRef = useRef<HTMLInputElement>(null);

  const handleCreate = useCallback(async () => {
    if (!name.trim()) return;

    if (source === 'script') {
      setShowScriptImport(true);
      return;
    }

    if (source === 'video') {
      videoInputRef.current?.click();
      return;
    }

    setCreating(true);
    await new Promise((r) => setTimeout(r, 200));
    onCreate(name.trim(), 'blank');
    setCreating(false);
  }, [name, source, onCreate]);

  const handleScriptImport = useCallback(
    (script: string, styleGuide: string, scenes: unknown[]) => {
      onCreate(name.trim(), 'script', { script, styleGuide, scenes });
      setShowScriptImport(false);
    },
    [name, onCreate]
  );

  const handleVideoSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        onCreate(name.trim(), 'video', { file });
      }
    },
    [name, onCreate]
  );

  if (showScriptImport) {
    return (
      <ScriptImportDialog
        onImport={handleScriptImport}
        onCancel={() => setShowScriptImport(false)}
      />
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />

      <div className="relative bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h2 className="text-sm font-semibold text-gray-100">New Storyboard Project</h2>
          <button onClick={onCancel} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-4">
          {/* Project name */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Project Name *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Storyboard"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') handleCreate(); }}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>

          {/* Source options */}
          <div className="grid grid-cols-3 gap-2">
            {SOURCE_OPTIONS.map((opt) => (
              <button
                key={opt.id}
                onClick={() => setSource(opt.id)}
                className={[
                  'flex flex-col items-center gap-2 p-4 rounded-xl border-2 text-center transition-all',
                  source === opt.id
                    ? 'border-blue-500 bg-blue-500/10 text-blue-300'
                    : 'border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600 hover:text-gray-200',
                ].join(' ')}
              >
                <span className="opacity-70">{opt.icon}</span>
                <div>
                  <p className="text-xs font-medium leading-tight">{opt.label}</p>
                  <p className="text-[10px] opacity-60 mt-0.5 leading-snug">{opt.description}</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-700">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors">
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={!name.trim() || creating}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {creating && <Loader2 size={14} className="animate-spin" />}
            {source === 'blank' ? 'Create' : source === 'script' ? 'Continue' : 'Choose Video'}
          </button>
        </div>
      </div>

      {/* Hidden video file input */}
      <input
        ref={videoInputRef}
        type="file"
        accept="video/*"
        className="hidden"
        onChange={handleVideoSelect}
      />
    </div>
  );
});

export default NewProjectDialog;
