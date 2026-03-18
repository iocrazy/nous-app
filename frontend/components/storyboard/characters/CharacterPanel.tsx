import React, { useState, useCallback, useEffect } from 'react';
import { X, Plus, Users, Loader2, AlertCircle } from 'lucide-react';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import { StoryboardCharacter } from '../../../types';
import {
  fetchCharacters,
  createCharacter as apiCreateCharacter,
  updateCharacter as apiUpdateCharacter,
  deleteCharacter as apiDeleteCharacter,
} from '../../../services/storyboardService';
import CharacterCard from './CharacterCard';
import CharacterEditor, { CharacterFormData } from './CharacterEditor';

// ─── Props ────────────────────────────────────────────────────────────────────

interface CharacterPanelProps {
  open: boolean;
  onClose: () => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

const CharacterPanel = React.memo(function CharacterPanel({
  open,
  onClose,
}: CharacterPanelProps) {
  const {
    characters,
    setCharacters,
    addCharacter,
    updateCharacter,
    removeCharacter,
    currentProjectId,
  } = useStoryboardStore();

  const [editorOpen, setEditorOpen] = useState(false);
  const [editingCharacter, setEditingCharacter] = useState<StoryboardCharacter | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Fetch characters on panel open
  useEffect(() => {
    if (!open || !currentProjectId) return;

    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const result = await fetchCharacters(currentProjectId!);
        if (!cancelled) {
          setCharacters(result ?? []);
        }
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err);
          setError(message);
          console.error('Failed to fetch characters:', err);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, [open, currentProjectId, setCharacters]);

  const handleNewCharacter = useCallback(() => {
    setEditingCharacter(null);
    setEditorOpen(true);
  }, []);

  const handleEdit = useCallback((character: StoryboardCharacter) => {
    setEditingCharacter(character);
    setEditorOpen(true);
  }, []);

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await apiDeleteCharacter(id);
        removeCharacter(id);
      } catch (err) {
        console.error('Failed to delete character:', err);
        setError(err instanceof Error ? err.message : 'Failed to delete character');
      }
    },
    [removeCharacter]
  );

  const handleSave = useCallback(
    async (data: CharacterFormData) => {
      if (!currentProjectId) return;

      setSaving(true);
      setError(null);
      try {
        if (editingCharacter) {
          const updated = await apiUpdateCharacter(editingCharacter.id, {
            name: data.name,
            description: data.description,
            visual_traits: data.visual_traits,
            reference_image_url: data.reference_image_url,
          });
          updateCharacter(editingCharacter.id, updated);
        } else {
          const created = await apiCreateCharacter(currentProjectId, {
            name: data.name,
            description: data.description,
            visual_traits: data.visual_traits,
          });
          addCharacter(created);
        }
        setEditorOpen(false);
        setEditingCharacter(null);
      } catch (err) {
        console.error('Failed to save character:', err);
        setError(err instanceof Error ? err.message : 'Failed to save character');
      } finally {
        setSaving(false);
      }
    },
    [editingCharacter, updateCharacter, addCharacter, currentProjectId]
  );

  const handleCancelEditor = useCallback(() => {
    setEditorOpen(false);
    setEditingCharacter(null);
  }, []);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-10 bg-black/30" onClick={onClose} />

      {/* Panel */}
      <div className="absolute left-0 top-0 h-full w-72 z-20 bg-gray-900 border-r border-gray-700 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <div className="flex items-center gap-2">
            <Users size={16} className="text-gray-400" />
            <h2 className="text-sm font-semibold text-gray-100">Characters</h2>
            <span className="text-xs text-gray-500">({characters.length})</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={handleNewCharacter}
              title="New Character"
              className="flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
            >
              <Plus size={12} /> New
            </button>
            <button
              onClick={onClose}
              title="Close"
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
            >
              <X size={15} />
            </button>
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="mx-3 mt-3 flex items-center gap-2 px-3 py-2 bg-red-900/30 border border-red-800 rounded-lg">
            <AlertCircle size={14} className="text-red-400 flex-shrink-0" />
            <p className="text-xs text-red-300 line-clamp-2">{error}</p>
            <button onClick={() => setError(null)} className="ml-auto text-red-400 hover:text-red-300">
              <X size={12} />
            </button>
          </div>
        )}

        {/* Character list */}
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-40">
              <Loader2 size={24} className="animate-spin text-blue-400 mb-2" />
              <p className="text-sm text-gray-500">Loading characters...</p>
            </div>
          ) : characters.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 text-center">
              <Users size={32} className="text-gray-700 mb-2" />
              <p className="text-sm text-gray-500">No characters yet</p>
              <p className="text-xs text-gray-600 mt-1">
                Click{' '}
                <span className="text-blue-400 font-medium">New</span> to add one
              </p>
            </div>
          ) : (
            characters.map((char) => (
              <CharacterCard
                key={char.id}
                character={char}
                onEdit={handleEdit}
                onDelete={handleDelete}
              />
            ))
          )}
        </div>
      </div>

      {/* Editor modal */}
      {editorOpen && (
        <CharacterEditor
          character={editingCharacter}
          onSave={handleSave}
          onCancel={handleCancelEditor}
          saving={saving}
        />
      )}
    </>
  );
});

export default CharacterPanel;
