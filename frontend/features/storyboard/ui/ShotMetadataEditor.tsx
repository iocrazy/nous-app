import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { Camera, Check, Clock, Sun, Type, X } from 'lucide-react';

import type { StoryboardFrame } from '../../../types';
import { updateFrame } from '../../../services/storyboardService';

// ─── Option definitions ──────────────────────────────────────────────────────

const SHOT_TYPES = ['wide','medium','close-up','extreme close-up','over-shoulder','POV','aerial','insert'] as const;
const CAMERA_ANGLES = ['eye-level','low-angle','high-angle','dutch-angle','bird\'s-eye','worm\'s-eye'] as const;
const CAMERA_MOVEMENTS = ['static','pan-left','pan-right','tilt-up','tilt-down','dolly-in','dolly-out','tracking','crane','handheld','zoom-in','zoom-out'] as const;
const LIGHTING_TYPES = ['natural','studio','dramatic','silhouette','backlit','low-key','high-key','golden-hour'] as const;
const TRANSITIONS = ['cut','fade','dissolve'] as const;

const DEBOUNCE_MS = 600;

// ─── Types ───────────────────────────────────────────────────────────────────

type UpdatableFields = Partial<Pick<
  StoryboardFrame,
  'note' | 'shot_type' | 'camera_angle' | 'camera_movement' |
  'focal_length' | 'lighting' | 'duration_seconds' | 'transition_type'
>>;

interface ShotMetadataEditorProps {
  frame: StoryboardFrame;
  onClose: () => void;
  onUpdate: (updated: StoryboardFrame) => void;
}

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function formatOptionLabel(value: string): string {
  return value
    .split('-')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function SelectField({
  label,
  icon,
  value,
  options,
  onChange,
}: {
  label: string;
  icon: React.ReactNode;
  value: string | undefined;
  options: readonly string[];
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1">
      <label className="flex items-center gap-1.5 text-xs font-medium text-neutral-400">
        {icon}
        {label}
      </label>
      <select
        className="h-8 w-full rounded-md border border-neutral-700 bg-neutral-800 px-2 text-sm text-neutral-200 outline-none transition-colors focus:border-blue-500"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">—</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {formatOptionLabel(opt)}
          </option>
        ))}
      </select>
    </div>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export const ShotMetadataEditor = memo(({
  frame,
  onClose,
  onUpdate,
}: ShotMetadataEditorProps) => {
  const [localFields, setLocalFields] = useState<UpdatableFields>({
    shot_type: frame.shot_type,
    camera_angle: frame.camera_angle,
    camera_movement: frame.camera_movement,
    focal_length: frame.focal_length,
    lighting: frame.lighting,
    duration_seconds: frame.duration_seconds,
    transition_type: frame.transition_type,
    note: frame.note,
  });
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestFieldsRef = useRef(localFields);
  latestFieldsRef.current = localFields;

  // Reset local state when frame prop changes
  useEffect(() => {
    setLocalFields({
      shot_type: frame.shot_type,
      camera_angle: frame.camera_angle,
      camera_movement: frame.camera_movement,
      focal_length: frame.focal_length,
      lighting: frame.lighting,
      duration_seconds: frame.duration_seconds,
      transition_type: frame.transition_type,
      note: frame.note,
    });
  }, [frame.id]);

  const persistFields = useCallback(async (fields: UpdatableFields) => {
    setSaveStatus('saving');
    try {
      const updated = await updateFrame(frame.id, fields);
      setSaveStatus('saved');
      onUpdate(updated);
      setTimeout(() => setSaveStatus('idle'), 1500);
    } catch (err) {
      console.error('[ShotMetadataEditor] save failed:', err);
      setSaveStatus('error');
      setTimeout(() => setSaveStatus('idle'), 2000);
    }
  }, [frame.id, onUpdate]);

  const scheduleAutoSave = useCallback((nextFields: UpdatableFields) => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      persistFields(nextFields);
    }, DEBOUNCE_MS);
  }, [persistFields]);

  // Cleanup debounce on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const handleFieldChange = useCallback(
    <K extends keyof UpdatableFields>(key: K, value: UpdatableFields[K]) => {
      const nextFields = { ...latestFieldsRef.current, [key]: value };
      setLocalFields(nextFields);
      scheduleAutoSave(nextFields);
    },
    [scheduleAutoSave],
  );

  const handleSelectChange = useCallback(
    (key: keyof UpdatableFields) => (value: string) => {
      handleFieldChange(key, value || undefined);
    },
    [handleFieldChange],
  );

  return (
    <div className="w-72 rounded-lg border border-neutral-700 bg-neutral-900 shadow-xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-neutral-700 px-3 py-2">
        <h3 className="text-sm font-semibold text-neutral-200">
          Shot Metadata
        </h3>
        <div className="flex items-center gap-2">
          {saveStatus === 'saving' && (
            <span className="text-[11px] text-neutral-500">Saving...</span>
          )}
          {saveStatus === 'saved' && (
            <span className="flex items-center gap-0.5 text-[11px] text-green-400">
              <Check className="h-3 w-3" />
              Saved
            </span>
          )}
          {saveStatus === 'error' && (
            <span className="text-[11px] text-red-400">Save failed</span>
          )}
          <button
            className="rounded p-0.5 text-neutral-400 transition-colors hover:bg-neutral-800 hover:text-neutral-200"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="space-y-3 p-3">
        {/* Shot Type */}
        <SelectField
          label="Shot Type"
          icon={<Camera className="h-3.5 w-3.5" />}
          value={localFields.shot_type}
          options={SHOT_TYPES}
          onChange={handleSelectChange('shot_type')}
        />

        {/* Camera Angle */}
        <SelectField
          label="Camera Angle"
          icon={<Camera className="h-3.5 w-3.5" />}
          value={localFields.camera_angle}
          options={CAMERA_ANGLES}
          onChange={handleSelectChange('camera_angle')}
        />

        {/* Camera Movement */}
        <SelectField
          label="Camera Movement"
          icon={<Camera className="h-3.5 w-3.5" />}
          value={localFields.camera_movement}
          options={CAMERA_MOVEMENTS}
          onChange={handleSelectChange('camera_movement')}
        />

        {/* Focal Length */}
        <div className="space-y-1">
          <label className="flex items-center gap-1.5 text-xs font-medium text-neutral-400">
            <Type className="h-3.5 w-3.5" />
            Focal Length
          </label>
          <input
            type="text"
            className="h-8 w-full rounded-md border border-neutral-700 bg-neutral-800 px-2 text-sm text-neutral-200 placeholder-neutral-500 outline-none transition-colors focus:border-blue-500"
            placeholder="e.g. 35mm"
            value={localFields.focal_length ?? ''}
            onChange={(e) => handleFieldChange('focal_length', e.target.value || undefined)}
            onBlur={() => persistFields(latestFieldsRef.current)}
          />
        </div>

        {/* Lighting */}
        <SelectField
          label="Lighting"
          icon={<Sun className="h-3.5 w-3.5" />}
          value={localFields.lighting}
          options={LIGHTING_TYPES}
          onChange={handleSelectChange('lighting')}
        />

        {/* Duration */}
        <div className="space-y-1">
          <label className="flex items-center gap-1.5 text-xs font-medium text-neutral-400">
            <Clock className="h-3.5 w-3.5" />
            Duration (seconds)
          </label>
          <input
            type="number"
            className="h-8 w-full rounded-md border border-neutral-700 bg-neutral-800 px-2 text-sm text-neutral-200 outline-none transition-colors focus:border-blue-500"
            min={0.5}
            max={30}
            step={0.5}
            value={localFields.duration_seconds ?? 2}
            onChange={(e) => {
              const parsed = parseFloat(e.target.value);
              if (Number.isFinite(parsed)) {
                handleFieldChange('duration_seconds', parsed);
              }
            }}
            onBlur={() => persistFields(latestFieldsRef.current)}
          />
        </div>

        {/* Transition */}
        <SelectField
          label="Transition"
          icon={<Camera className="h-3.5 w-3.5" />}
          value={localFields.transition_type}
          options={TRANSITIONS}
          onChange={handleSelectChange('transition_type')}
        />

        {/* Note */}
        <div className="space-y-1">
          <label className="flex items-center gap-1.5 text-xs font-medium text-neutral-400">
            <Type className="h-3.5 w-3.5" />
            Note
          </label>
          <textarea
            className="min-h-[64px] w-full resize-y rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1.5 text-sm text-neutral-200 placeholder-neutral-500 outline-none transition-colors focus:border-blue-500"
            placeholder="Shot notes..."
            value={localFields.note ?? ''}
            onChange={(e) => handleFieldChange('note', e.target.value || undefined)}
            onBlur={() => persistFields(latestFieldsRef.current)}
            rows={3}
          />
        </div>
      </div>
    </div>
  );
});

ShotMetadataEditor.displayName = 'ShotMetadataEditor';
