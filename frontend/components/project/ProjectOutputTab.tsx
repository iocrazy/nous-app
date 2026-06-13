import { useState, useEffect, useCallback } from 'react';
import { Download, Plus, Palette, Trash2, Edit2, Check, X, FileText, Clapperboard } from 'lucide-react';
import {
  fetchStyleTemplates,
  createStyleTemplate,
  deleteStyleTemplate,
} from '../../services/styleTemplateService';
import type { StyleTemplate } from '../../types';

interface Props {
  projectId: string;
}

export function ProjectOutputTab({ projectId }: Props) {
  const [templates, setTemplates] = useState<StyleTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newPrompt, setNewPrompt] = useState('');
  const [newCategory, setNewCategory] = useState('');

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchStyleTemplates();
      setTemplates(result);
    } catch (err) {
      console.error('Failed to load style templates:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  const handleCreate = async () => {
    if (!newName.trim() || !newPrompt.trim()) return;
    try {
      await createStyleTemplate({
        name: newName.trim(),
        prompt_content: newPrompt.trim(),
        category: newCategory.trim() || undefined,
      });
      setNewName('');
      setNewPrompt('');
      setNewCategory('');
      setShowCreate(false);
      await loadTemplates();
    } catch (err) {
      console.error('Failed to create template:', err);
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm('Delete this template?')) return;
    try {
      await deleteStyleTemplate(id);
      setTemplates((prev) => prev.filter((t) => t.id !== id));
    } catch (err) {
      console.error('Failed to delete template:', err);
    }
  };

  return (
    <div className="space-y-6">
      {/* Export section */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-ink-400">Export</h3>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            className="flex items-center gap-2 px-4 py-3 rounded-xl border border-ink-800 bg-ink-900 hover:border-ink-600 transition-colors"
            disabled
          >
            <FileText size={20} className="text-indigo-400" />
            <div className="text-left">
              <p className="text-xs font-medium text-ink-200">Export Script PDF</p>
              <p className="text-[10px] text-ink-500">Coming soon</p>
            </div>
          </button>
          <button
            className="flex items-center gap-2 px-4 py-3 rounded-xl border border-ink-800 bg-ink-900 hover:border-ink-600 transition-colors"
            disabled
          >
            <Clapperboard size={20} className="text-blue-400" />
            <div className="text-left">
              <p className="text-xs font-medium text-ink-200">Export Storyboard ZIP</p>
              <p className="text-[10px] text-ink-500">Coming soon</p>
            </div>
          </button>
          <button
            className="flex items-center gap-2 px-4 py-3 rounded-xl border border-ink-800 bg-ink-900 hover:border-ink-600 transition-colors"
            disabled
          >
            <Download size={20} className="text-green-400" />
            <div className="text-left">
              <p className="text-xs font-medium text-ink-200">Export All</p>
              <p className="text-[10px] text-ink-500">Coming soon</p>
            </div>
          </button>
        </div>
      </div>

      {/* Style Templates section */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-ink-400">
            <Palette size={14} className="inline mr-1.5" />
            Style Templates ({templates.length})
          </h3>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
          >
            <Plus size={14} />
            New Template
          </button>
        </div>

        {showCreate && (
          <div className="mb-4 p-4 rounded-xl border border-ink-700 bg-ink-900 space-y-3">
            <input
              className="w-full bg-ink-800 text-sm text-ink-200 rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-indigo-500/50"
              placeholder="Template name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <input
              className="w-full bg-ink-800 text-sm text-ink-200 rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-indigo-500/50"
              placeholder="Category (optional)"
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value)}
            />
            <textarea
              className="w-full bg-ink-800 text-sm text-ink-200 rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-indigo-500/50 min-h-[80px]"
              placeholder="Prompt content — describe the visual style, mood, lighting..."
              value={newPrompt}
              onChange={(e) => setNewPrompt(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowCreate(false)}
                className="px-3 py-1.5 text-xs text-ink-400 hover:text-ink-200 rounded-lg hover:bg-ink-800"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={!newName.trim() || !newPrompt.trim()}
                className="px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50"
              >
                Create
              </button>
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="w-[260px] h-[120px] rounded-xl bg-ink-900 animate-pulse" />
            ))}
          </div>
        ) : templates.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 gap-2 text-center">
            <Palette size={32} className="text-ink-700" />
            <p className="text-sm text-ink-500">No style templates yet</p>
            <p className="text-xs text-ink-600">Create templates to save reusable AI prompts</p>
          </div>
        ) : (
          <div className="flex flex-wrap gap-3">
            {templates.map((tpl) => (
              <div
                key={tpl.id}
                className="w-[260px] rounded-xl border border-ink-800 bg-ink-900 p-3 group"
              >
                <div className="flex items-start justify-between">
                  <h4 className="text-sm font-medium text-ink-200 truncate flex-1">{tpl.name}</h4>
                  <button
                    onClick={() => handleDelete(tpl.id)}
                    className="hidden group-hover:block text-ink-600 hover:text-red-400 p-0.5"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
                {tpl.category && (
                  <span className="inline-block mt-1 text-[10px] px-1.5 py-0.5 rounded bg-ink-800 text-ink-400">
                    {tpl.category}
                  </span>
                )}
                <p className="mt-2 text-[11px] text-ink-500 line-clamp-3">{tpl.prompt_content}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
