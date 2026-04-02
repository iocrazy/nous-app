import { useState, useEffect, useCallback } from 'react';
import { ArrowLeft, Save } from 'lucide-react';
import MDEditor from '@uiw/react-md-editor';
import { fetchSkillDetail, createSkill, updateSkill } from '../../services/skillService';
import { useToast } from '../../contexts/ToastContext';

const CATEGORIES = ['script', 'storyboard', 'copywriting', 'general'];
const ICONS = ['✨', '🎬', '🎞️', '✍️', '📱', '🔄', '📝', '🎯', '💡', '🎨'];
const MAX_CONTENT_LENGTH = 10000;

interface SkillEditorProps {
  skillId: string | null;
  projectId: string;
  onClose: () => void;
}

export function SkillEditor({ skillId, projectId, onClose }: SkillEditorProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [contentMd, setContentMd] = useState('');
  const [category, setCategory] = useState('general');
  const [icon, setIcon] = useState('✨');
  const [outputFormat, setOutputFormat] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [isProjectScope, setIsProjectScope] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [dirty, setDirty] = useState(false);
  const { addToast } = useToast();

  const isNew = !skillId;

  // Load existing skill
  useEffect(() => {
    if (!skillId) return;
    let cancelled = false;
    setLoadingDetail(true);
    fetchSkillDetail(skillId)
      .then((skill) => {
        if (cancelled) return;
        setName(skill.name);
        setDescription(skill.description || '');
        setContentMd(skill.content_md || '');
        setCategory(skill.category || 'general');
        setIcon(skill.icon || '✨');
        setOutputFormat(skill.output_format || '');
        setIsPublic(skill.is_public);
        setIsProjectScope(!!skill.project_id);
      })
      .catch((err) => {
        if (!cancelled) addToast('Failed to load skill', 'error');
        console.error('[SkillEditor] load failed:', err);
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => { cancelled = true; };
  }, [skillId, addToast]);

  // Track dirty state
  useEffect(() => {
    if (!loadingDetail && (name || contentMd)) setDirty(true);
  }, [name, description, contentMd, category, icon, outputFormat, isPublic, isProjectScope, loadingDetail]);

  // Warn on navigate-away
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const handleSave = useCallback(async () => {
    if (!name.trim()) {
      addToast('Skill name is required', 'error');
      return;
    }
    if (!contentMd.trim()) {
      addToast('Skill content is required', 'error');
      return;
    }
    if (contentMd.length > MAX_CONTENT_LENGTH) {
      addToast(`Content too long (max ${MAX_CONTENT_LENGTH} chars)`, 'error');
      return;
    }

    setSaving(true);
    try {
      const data = {
        name: name.trim(),
        description: description.trim() || undefined,
        content_md: contentMd,
        category,
        icon,
        output_format: outputFormat.trim() || undefined,
        is_public: isPublic,
        project_id: isProjectScope ? projectId : undefined,
      };

      if (isNew) {
        await createSkill(data);
        addToast('Skill created', 'success');
      } else {
        await updateSkill(skillId, data);
        addToast('Skill updated', 'success');
      }
      setDirty(false);
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to save skill';
      addToast(msg, 'error');
    } finally {
      setSaving(false);
    }
  }, [name, description, contentMd, category, icon, outputFormat, isPublic, isProjectScope, projectId, isNew, skillId, onClose, addToast]);

  const handleClose = useCallback(() => {
    if (dirty && !window.confirm('You have unsaved changes. Discard?')) return;
    onClose();
  }, [dirty, onClose]);

  if (loadingDetail) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-600 border-t-violet-500" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between border-b border-zinc-800 px-6 py-3">
        <button
          onClick={handleClose}
          className="flex items-center gap-2 text-sm text-zinc-400 transition-colors hover:text-zinc-100"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Skills
        </button>
        <div className="flex items-center gap-2">
          <button
            onClick={handleClose}
            className="rounded-md px-3 py-1.5 text-sm text-zinc-400 transition-colors hover:text-zinc-100"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim() || !contentMd.trim()}
            className="flex items-center gap-2 rounded-md bg-violet-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-violet-500 disabled:opacity-40"
          >
            <Save className="h-3.5 w-3.5" />
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {/* Editor body — two columns */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left column: metadata */}
        <div className="w-72 shrink-0 space-y-5 overflow-y-auto border-r border-zinc-800 p-6">
          {/* Name */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Skill"
              maxLength={200}
              className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-violet-500"
            />
          </div>

          {/* Category */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Category</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-violet-500"
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          {/* Description */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this skill do?"
              rows={3}
              maxLength={2000}
              className="w-full resize-none rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-violet-500"
            />
          </div>

          {/* Icon */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Icon</label>
            <div className="flex flex-wrap gap-1.5">
              {ICONS.map((i) => (
                <button
                  key={i}
                  onClick={() => setIcon(i)}
                  className={`flex h-8 w-8 items-center justify-center rounded-md text-lg transition-colors ${
                    icon === i ? 'bg-violet-600 ring-2 ring-violet-400' : 'bg-zinc-800 hover:bg-zinc-700'
                  }`}
                >
                  {i}
                </button>
              ))}
            </div>
          </div>

          {/* Scope */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Scope</label>
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm text-zinc-300">
                <input
                  type="radio"
                  checked={isProjectScope}
                  onChange={() => setIsProjectScope(true)}
                  className="accent-violet-500"
                />
                This Project Only
              </label>
              <label className="flex items-center gap-2 text-sm text-zinc-300">
                <input
                  type="radio"
                  checked={!isProjectScope}
                  onChange={() => setIsProjectScope(false)}
                  className="accent-violet-500"
                />
                Global (all projects)
              </label>
            </div>
          </div>

          {/* Visibility */}
          <div>
            <label className="flex items-center gap-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={isPublic}
                onChange={(e) => setIsPublic(e.target.checked)}
                className="accent-violet-500"
              />
              Public (visible to team)
            </label>
          </div>
        </div>

        {/* Right column: content editors */}
        <div className="flex flex-1 flex-col overflow-y-auto p-6">
          {/* Content MD */}
          <div className="mb-6 flex-1">
            <div className="mb-1.5 flex items-center justify-between">
              <label className="text-xs font-medium text-zinc-400">Content (Markdown)</label>
              <span className={`text-[10px] ${contentMd.length > MAX_CONTENT_LENGTH ? 'text-red-400' : 'text-zinc-500'}`}>
                {contentMd.length}/{MAX_CONTENT_LENGTH}
              </span>
            </div>
            <div data-color-mode="dark">
              <MDEditor
                value={contentMd}
                onChange={(val) => setContentMd(val || '')}
                height={400}
                preview="edit"
              />
            </div>
          </div>

          {/* Output Format */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">
              Output Format (optional)
            </label>
            <textarea
              value={outputFormat}
              onChange={(e) => setOutputFormat(e.target.value)}
              placeholder="Describe the expected output format..."
              rows={4}
              maxLength={5000}
              className="w-full resize-none rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-violet-500"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
