import React, { useState, useCallback } from 'react';
import { User, Edit2, Trash2 } from 'lucide-react';
import { StoryboardCharacter } from '../../../types';

// ─── Props ────────────────────────────────────────────────────────────────────

interface CharacterCardProps {
  character: StoryboardCharacter;
  frameCount?: number;
  onEdit: (character: StoryboardCharacter) => void;
  onDelete: (id: string) => void;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function buildTraitSummary(visualTraits?: Record<string, string>): string {
  if (!visualTraits) return '';
  const entries = Object.entries(visualTraits).slice(0, 3);
  return entries.map(([, v]) => v).filter(Boolean).join(', ');
}

// ─── Component ────────────────────────────────────────────────────────────────

const CharacterCard = React.memo(function CharacterCard({
  character,
  frameCount = 0,
  onEdit,
  onDelete,
}: CharacterCardProps) {
  const [hovered, setHovered] = useState(false);

  const handleEdit = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onEdit(character);
    },
    [character, onEdit]
  );

  const handleDelete = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onDelete(character.id);
    },
    [character.id, onDelete]
  );

  const traitSummary = buildTraitSummary(character.visual_traits);

  return (
    <div
      className="relative flex items-center gap-3 p-3 rounded-xl bg-gray-800 border border-gray-700 hover:border-gray-600 transition-all cursor-default"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Avatar */}
      <div className="flex-shrink-0 w-12 h-12 rounded-lg overflow-hidden bg-gray-700 flex items-center justify-center">
        {character.thumbnail_url || character.reference_image_url ? (
          <img
            src={character.thumbnail_url ?? character.reference_image_url}
            alt={character.name}
            className="w-full h-full object-cover"
          />
        ) : (
          <User size={24} className="text-gray-500" />
        )}
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-100 truncate">{character.name}</p>
        {traitSummary && (
          <p className="text-xs text-gray-400 truncate mt-0.5">{traitSummary}</p>
        )}
        <p className="text-xs text-gray-500 mt-0.5">
          Used in {frameCount} frame{frameCount !== 1 ? 's' : ''}
        </p>
      </div>

      {/* Hover actions */}
      {hovered && (
        <div className="flex items-center gap-1">
          <button
            onClick={handleEdit}
            title="Edit character"
            className="p-1.5 rounded-lg text-gray-400 hover:text-blue-400 hover:bg-gray-700 transition-colors"
          >
            <Edit2 size={14} />
          </button>
          <button
            onClick={handleDelete}
            title="Delete character"
            className="p-1.5 rounded-lg text-gray-400 hover:text-red-400 hover:bg-gray-700 transition-colors"
          >
            <Trash2 size={14} />
          </button>
        </div>
      )}
    </div>
  );
});

export default CharacterCard;
