import React, { useState, useCallback } from 'react';
import { X, Plus, Users } from 'lucide-react';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import { StoryboardCharacter } from '../../../types';
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
  const { characters, addCharacter, updateCharacter, removeCharacter, currentProjectId } =
    useStoryboardStore();

  const [editorOpen, setEditorOpen] = useState(false);
  const [editingCharacter, setEditingCharacter] = useState<StoryboardCharacter | null>(null);

  const handleNewCharacter = useCallback(() => {
    setEditingCharacter(null);
    setEditorOpen(true);
  }, []);

  const handleEdit = useCallback((character: StoryboardCharacter) => {
    setEditingCharacter(character);
    setEditorOpen(true);
  }, []);

  const handleDelete = useCallback(
    (id: string) => {
      removeCharacter(id);
    },
    [removeCharacter]
  );

  const handleSave = useCallback(
    (data: CharacterFormData) => {
      if (editingCharacter) {
        updateCharacter(editingCharacter.id, {
          name: data.name,
          description: data.description,
          visual_traits: data.visual_traits,
          reference_image_url: data.reference_image_url,
          updated_at: new Date().toISOString(),
        });
      } else {
        const newChar: StoryboardCharacter = {
          id: `char-${Date.now()}`,
          project_id: currentProjectId ?? '',
          name: data.name,
          description: data.description,
          visual_traits: data.visual_traits,
          reference_image_url: data.reference_image_url,
          sort_order: characters.length,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        addCharacter(newChar);
      }
      setEditorOpen(false);
      setEditingCharacter(null);
    },
    [editingCharacter, updateCharacter, addCharacter, characters.length, currentProjectId]
  );

  const handleCancelEditor = useCallback(() => {
    setEditorOpen(false);
    setEditingCharacter(null);
  }, []);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-10 bg-black/30"
        onClick={onClose}
      />

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

        {/* Character list */}
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
          {characters.length === 0 ? (
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
        />
      )}
    </>
  );
});

export default CharacterPanel;
