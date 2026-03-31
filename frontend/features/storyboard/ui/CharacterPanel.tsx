import { useCallback, useEffect, useState } from 'react';
import { Users, Plus, Trash2, X, ChevronDown, ChevronUp } from 'lucide-react';

import {
  fetchCharacters,
  createCharacter,
  updateCharacter,
  deleteCharacter,
} from '../../../services/storyboardService';
import type { StoryboardCharacter } from '../../../types';

// ─── Types ───────────────────────────────────────────────────────────────────
interface CharacterPanelProps {
  projectId: string;
  onClose: () => void;
}
interface TraitEntry {
  key: string;
  value: string;
}
interface CharacterDraft {
  name: string;
  description: string;
  traits: TraitEntry[];
}

// ─── Helpers ─────────────────────────────────────────────────────────────────
const EMPTY_DRAFT: CharacterDraft = { name: '', description: '', traits: [] };

function traitsToEntries(traits?: Record<string, string>): TraitEntry[] {
  if (!traits) return [];
  return Object.entries(traits).map(([key, value]) => ({ key, value }));
}

function entriesToTraits(entries: TraitEntry[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const { key, value } of entries) {
    const k = key.trim();
    if (k) result[k] = value.trim();
  }
  return result;
}

// ─── Visual Trait Editor ─────────────────────────────────────────────────────
function TraitEditor({
  traits,
  onChange,
}: {
  traits: TraitEntry[];
  onChange: (next: TraitEntry[]) => void;
}) {
  const handleAdd = () => onChange([...traits, { key: '', value: '' }]);

  const handleRemove = (index: number) =>
    onChange(traits.filter((_, i) => i !== index));

  const handleChange = (index: number, field: 'key' | 'value', val: string) =>
    onChange(traits.map((t, i) => (i === index ? { ...t, [field]: val } : t)));

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-zinc-400">Visual Traits</span>
        <button
          type="button"
          onClick={handleAdd}
          className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300"
        >
          <Plus size={12} /> Add Trait
        </button>
      </div>
      {traits.length === 0 && (
        <p className="text-xs text-zinc-500 italic">No traits defined</p>
      )}
      {traits.map((trait, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <input
            type="text"
            placeholder="key"
            value={trait.key}
            onChange={(e) => handleChange(i, 'key', e.target.value)}
            className="flex-1 rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-200 outline-none focus:ring-1 focus:ring-blue-500"
          />
          <span className="text-zinc-500 text-xs">:</span>
          <input
            type="text"
            placeholder="value"
            value={trait.value}
            onChange={(e) => handleChange(i, 'value', e.target.value)}
            className="flex-1 rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-200 outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            type="button"
            onClick={() => handleRemove(i)}
            className="p-0.5 text-zinc-500 hover:text-red-400"
          >
            <X size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}

// ─── Character Card ──────────────────────────────────────────────────────────
function CharacterCard({
  character,
  isExpanded,
  onToggle,
  onSave,
  onDelete,
}: {
  character: StoryboardCharacter;
  isExpanded: boolean;
  onToggle: () => void;
  onSave: (id: string, draft: CharacterDraft) => Promise<void>;
  onDelete: (id: string) => void;
}) {
  const [draft, setDraft] = useState<CharacterDraft>({
    name: character.name,
    description: character.description ?? '',
    traits: traitsToEntries(character.visual_traits),
  });
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Sync draft when character prop changes (after external save)
  useEffect(() => {
    setDraft({
      name: character.name,
      description: character.description ?? '',
      traits: traitsToEntries(character.visual_traits),
    });
  }, [character]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(character.id, draft);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteClick = () => {
    if (confirmDelete) {
      onDelete(character.id);
      setConfirmDelete(false);
    } else {
      setConfirmDelete(true);
    }
  };

  const thumbnailUrl = character.thumbnail_url ?? character.reference_image_url;

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800/60">
      {/* Collapsed row */}
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 p-3 text-left hover:bg-zinc-700/40 transition-colors rounded-lg"
      >
        {thumbnailUrl ? (
          <img
            src={thumbnailUrl}
            alt={character.name}
            className="h-9 w-9 rounded-full object-cover flex-shrink-0"
          />
        ) : (
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-zinc-700 flex-shrink-0">
            <Users size={16} className="text-zinc-400" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-zinc-200 truncate">
            {character.name}
          </p>
          {character.description && (
            <p className="text-xs text-zinc-400 truncate">
              {character.description}
            </p>
          )}
        </div>
        {isExpanded ? (
          <ChevronUp size={14} className="text-zinc-400 flex-shrink-0" />
        ) : (
          <ChevronDown size={14} className="text-zinc-400 flex-shrink-0" />
        )}
      </button>

      {/* Expanded editor */}
      {isExpanded && (
        <div className="border-t border-zinc-700 p-3 space-y-3">
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">
              Name
            </label>
            <input
              type="text"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              className="w-full rounded bg-zinc-800 px-2.5 py-1.5 text-sm text-zinc-200 outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">
              Description
            </label>
            <textarea
              rows={3}
              value={draft.description}
              onChange={(e) =>
                setDraft({ ...draft, description: e.target.value })
              }
              className="w-full rounded bg-zinc-800 px-2.5 py-1.5 text-sm text-zinc-200 outline-none resize-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

          <TraitEditor
            traits={draft.traits}
            onChange={(traits) => setDraft({ ...draft, traits })}
          />

          <div className="flex items-center justify-between pt-1">
            <button
              type="button"
              onClick={handleDeleteClick}
              onBlur={() => setConfirmDelete(false)}
              className={`flex items-center gap-1 text-xs px-2 py-1 rounded transition-colors ${
                confirmDelete
                  ? 'bg-red-600 text-white'
                  : 'text-zinc-400 hover:text-red-400'
              }`}
            >
              <Trash2 size={12} />
              {confirmDelete ? 'Confirm' : 'Delete'}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !draft.name.trim()}
              className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main Panel ──────────────────────────────────────────────────────────────

export function CharacterPanel({ projectId, onClose }: CharacterPanelProps) {
  const [characters, setCharacters] = useState<StoryboardCharacter[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const loadCharacters = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchCharacters(projectId);
      setCharacters(data);
    } catch (err) {
      console.error('Failed to load characters:', err);
      setError('Failed to load characters');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    loadCharacters();
  }, [loadCharacters]);

  const handleCreate = async () => {
    setCreating(true);
    try {
      const created = await createCharacter(projectId, {
        name: 'New Character',
      });
      setCharacters((prev) => [...prev, created]);
      setExpandedId(created.id);
    } catch (err) {
      console.error('Failed to create character:', err);
      setError('Failed to create character');
    } finally {
      setCreating(false);
    }
  };

  const handleSave = async (id: string, draft: CharacterDraft) => {
    const updated = await updateCharacter(id, {
      name: draft.name.trim(),
      description: draft.description.trim() || undefined,
      visual_traits: entriesToTraits(draft.traits),
    });
    setCharacters((prev) =>
      prev.map((c) => (c.id === id ? updated : c))
    );
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteCharacter(id);
      setCharacters((prev) => prev.filter((c) => c.id !== id));
      if (expandedId === id) setExpandedId(null);
    } catch (err) {
      console.error('Failed to delete character:', err);
      setError('Failed to delete character');
    }
  };

  const handleToggle = (id: string) =>
    setExpandedId((prev) => (prev === id ? null : id));

  return (
    <div className="flex h-full w-72 flex-col border-l border-zinc-700 bg-zinc-900">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-700 px-4 py-3">
        <div className="flex items-center gap-2">
          <Users size={16} className="text-zinc-400" />
          <h2 className="text-sm font-semibold text-zinc-200">Characters</h2>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200 transition-colors"
          aria-label="Close panel"
        >
          <X size={16} />
        </button>
      </div>

      {/* Add button */}
      <div className="px-3 pt-3">
        <button
          type="button"
          onClick={handleCreate}
          disabled={creating}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-zinc-600 py-2 text-xs font-medium text-zinc-400 hover:border-zinc-500 hover:text-zinc-300 disabled:opacity-40 transition-colors"
        >
          <Plus size={14} />
          {creating ? 'Adding...' : 'Add Character'}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
        {error && (
          <div className="rounded bg-red-900/30 px-3 py-2 text-xs text-red-300">
            {error}
            <button
              type="button"
              onClick={() => setError(null)}
              className="ml-2 underline hover:no-underline"
            >
              Dismiss
            </button>
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center py-8">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
          </div>
        )}

        {!loading && characters.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <Users size={28} className="mb-2 text-zinc-600" />
            <p className="text-sm text-zinc-400">No characters yet</p>
            <p className="text-xs text-zinc-500 mt-1">
              Add a character to get started
            </p>
          </div>
        )}

        {!loading &&
          characters.map((char) => (
            <CharacterCard
              key={char.id}
              character={char}
              isExpanded={expandedId === char.id}
              onToggle={() => handleToggle(char.id)}
              onSave={handleSave}
              onDelete={handleDelete}
            />
          ))}
      </div>
    </div>
  );
}
