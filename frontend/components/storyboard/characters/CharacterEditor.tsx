import React, { useState, useCallback, useEffect, useRef } from 'react';
import { X, Plus, Trash2, Upload } from 'lucide-react';
import { StoryboardCharacter } from '../../../types';

// ─── Types ────────────────────────────────────────────────────────────────────

interface TraitPair {
  key: string;
  value: string;
}

interface CharacterEditorProps {
  character?: StoryboardCharacter | null;
  onSave: (data: CharacterFormData) => void;
  onCancel: () => void;
}

export interface CharacterFormData {
  name: string;
  description: string;
  visual_traits: Record<string, string>;
  reference_image_url?: string;
}

const DEFAULT_TRAIT_KEYS = ['hair', 'clothing', 'body_type', 'age'];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function traitsToRows(traits?: Record<string, string>): TraitPair[] {
  if (!traits || Object.keys(traits).length === 0) {
    return DEFAULT_TRAIT_KEYS.map((key) => ({ key, value: '' }));
  }
  return Object.entries(traits).map(([key, value]) => ({ key, value }));
}

function rowsToTraits(rows: TraitPair[]): Record<string, string> {
  return Object.fromEntries(
    rows.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.value.trim()])
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

const CharacterEditor = React.memo(function CharacterEditor({
  character,
  onSave,
  onCancel,
}: CharacterEditorProps) {
  const [name, setName] = useState(character?.name ?? '');
  const [description, setDescription] = useState(character?.description ?? '');
  const [traitRows, setTraitRows] = useState<TraitPair[]>(() =>
    traitsToRows(character?.visual_traits)
  );
  const [referenceUrl, setReferenceUrl] = useState(
    character?.reference_image_url ?? ''
  );
  const [dragOver, setDragOver] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Reset when character changes
  useEffect(() => {
    setName(character?.name ?? '');
    setDescription(character?.description ?? '');
    setTraitRows(traitsToRows(character?.visual_traits));
    setReferenceUrl(character?.reference_image_url ?? '');
  }, [character]);

  const handleTraitKeyChange = useCallback((index: number, newKey: string) => {
    setTraitRows((prev) =>
      prev.map((row, i) => (i === index ? { ...row, key: newKey } : row))
    );
  }, []);

  const handleTraitValueChange = useCallback((index: number, newValue: string) => {
    setTraitRows((prev) =>
      prev.map((row, i) => (i === index ? { ...row, value: newValue } : row))
    );
  }, []);

  const handleAddTrait = useCallback(() => {
    setTraitRows((prev) => [...prev, { key: '', value: '' }]);
  }, []);

  const handleRemoveTrait = useCallback((index: number) => {
    setTraitRows((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleFileSelect = useCallback((file: File) => {
    const url = URL.createObjectURL(file);
    setReferenceUrl(url);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file?.type.startsWith('image/')) handleFileSelect(file);
    },
    [handleFileSelect]
  );

  const handleSave = useCallback(() => {
    if (!name.trim()) return;
    onSave({
      name: name.trim(),
      description: description.trim(),
      visual_traits: rowsToTraits(traitRows),
      reference_image_url: referenceUrl || undefined,
    });
  }, [name, description, traitRows, referenceUrl, onSave]);

  const isNew = !character;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />

      {/* Dialog */}
      <div className="relative w-full max-w-lg bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h2 className="text-base font-semibold text-gray-100">
            {isNew ? 'New Character' : 'Edit Character'}
          </h2>
          <button
            onClick={onCancel}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Name */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Name *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Character name"
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Brief character description..."
              rows={3}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors resize-none"
            />
          </div>

          {/* Visual traits */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-medium text-gray-400">Visual Traits</label>
              <button
                onClick={handleAddTrait}
                className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
              >
                <Plus size={12} /> Add trait
              </button>
            </div>
            <div className="space-y-2">
              {traitRows.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input
                    type="text"
                    value={row.key}
                    onChange={(e) => handleTraitKeyChange(i, e.target.value)}
                    placeholder="trait"
                    className="w-28 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-gray-500"
                  />
                  <input
                    type="text"
                    value={row.value}
                    onChange={(e) => handleTraitValueChange(i, e.target.value)}
                    placeholder="value"
                    className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-gray-500"
                  />
                  <button
                    onClick={() => handleRemoveTrait(i)}
                    className="p-1 text-gray-600 hover:text-red-400 transition-colors"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Reference image upload */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              Reference Image
            </label>
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={[
                'relative border-2 border-dashed rounded-xl p-4 text-center cursor-pointer transition-colors',
                dragOver
                  ? 'border-blue-500 bg-blue-500/10'
                  : 'border-gray-700 hover:border-gray-600',
              ].join(' ')}
            >
              {referenceUrl ? (
                <img
                  src={referenceUrl}
                  alt="Reference"
                  className="mx-auto h-32 object-contain rounded-lg"
                />
              ) : (
                <div className="flex flex-col items-center gap-2 text-gray-500">
                  <Upload size={20} />
                  <p className="text-xs">Drop an image or click to upload</p>
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) handleFileSelect(file);
                }}
              />
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-700">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!name.trim()}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {isNew ? 'Create' : 'Save Changes'}
          </button>
        </div>
      </div>
    </div>
  );
});

export default CharacterEditor;
