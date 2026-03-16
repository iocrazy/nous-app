import React, { useState, useCallback, useEffect } from 'react';
import { X } from 'lucide-react';
import { StoryboardFrame } from '../../../types';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface ShotMetadata {
  shot_type?: string;
  camera_angle?: string;
  camera_movement?: string;
  focal_length?: string;
  lighting?: string;
  duration_seconds: number;
  transition_type: 'cut' | 'fade' | 'dissolve';
}

interface ShotMetadataEditorProps {
  frame: Partial<StoryboardFrame>;
  onSave: (data: ShotMetadata) => void;
  onCancel: () => void;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const SHOT_TYPES = [
  'Extreme Close-up', 'Close-up', 'Medium Close-up', 'Medium',
  'Medium Wide', 'Wide', 'Extreme Wide',
];

const CAMERA_ANGLES = [
  'Eye Level', 'Low Angle', 'High Angle', "Bird's Eye", "Worm's Eye",
];

const CAMERA_MOVEMENTS = [
  'Static', 'Push', 'Pull', 'Pan', 'Tilt', 'Dolly', 'Crane', 'Tracking',
];

const FOCAL_LENGTHS = ['24mm', '35mm', '50mm', '85mm', '135mm'];

const TRANSITION_TYPES = [
  { value: 'cut', label: 'Cut' },
  { value: 'fade', label: 'Fade' },
  { value: 'dissolve', label: 'Dissolve' },
] as const;

// ─── Sub-components ───────────────────────────────────────────────────────────

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs font-medium text-gray-400 mb-1">{children}</label>;
}

function Select({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 focus:outline-none focus:border-blue-500 transition-colors"
    >
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

const ShotMetadataEditor = React.memo(function ShotMetadataEditor({
  frame,
  onSave,
  onCancel,
}: ShotMetadataEditorProps) {
  const [shotType, setShotType] = useState(frame.shot_type ?? '');
  const [cameraAngle, setCameraAngle] = useState(frame.camera_angle ?? '');
  const [cameraMovement, setCameraMovement] = useState(frame.camera_movement ?? '');
  const [focalLength, setFocalLength] = useState(frame.focal_length ?? '');
  const [lighting, setLighting] = useState(frame.lighting ?? '');
  const [duration, setDuration] = useState(frame.duration_seconds ?? 3);
  const [transition, setTransition] = useState<ShotMetadata['transition_type']>(
    frame.transition_type ?? 'cut'
  );

  useEffect(() => {
    setShotType(frame.shot_type ?? '');
    setCameraAngle(frame.camera_angle ?? '');
    setCameraMovement(frame.camera_movement ?? '');
    setFocalLength(frame.focal_length ?? '');
    setLighting(frame.lighting ?? '');
    setDuration(frame.duration_seconds ?? 3);
    setTransition(frame.transition_type ?? 'cut');
  }, [frame]);

  const handleSave = useCallback(() => {
    onSave({
      shot_type: shotType || undefined,
      camera_angle: cameraAngle || undefined,
      camera_movement: cameraMovement || undefined,
      focal_length: focalLength || undefined,
      lighting: lighting || undefined,
      duration_seconds: Math.max(0.5, Math.min(30, duration)),
      transition_type: transition,
    });
  }, [shotType, cameraAngle, cameraMovement, focalLength, lighting, duration, transition, onSave]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />

      <div className="relative bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h2 className="text-sm font-semibold text-gray-100">Shot Metadata</h2>
          <button onClick={onCancel} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Form */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          <div>
            <FieldLabel>Shot Type</FieldLabel>
            <Select value={shotType} onChange={setShotType} options={SHOT_TYPES} placeholder="— select —" />
          </div>

          <div>
            <FieldLabel>Camera Angle</FieldLabel>
            <Select value={cameraAngle} onChange={setCameraAngle} options={CAMERA_ANGLES} placeholder="— select —" />
          </div>

          <div>
            <FieldLabel>Camera Movement</FieldLabel>
            <Select value={cameraMovement} onChange={setCameraMovement} options={CAMERA_MOVEMENTS} placeholder="— select —" />
          </div>

          <div>
            <FieldLabel>Focal Length</FieldLabel>
            <Select value={focalLength} onChange={setFocalLength} options={FOCAL_LENGTHS} placeholder="— select —" />
          </div>

          <div>
            <FieldLabel>Lighting</FieldLabel>
            <input
              type="text"
              value={lighting}
              onChange={(e) => setLighting(e.target.value)}
              placeholder="e.g. Natural, Studio, Backlit..."
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>

          <div>
            <FieldLabel>Duration (seconds)</FieldLabel>
            <input
              type="number"
              min={0.5}
              max={30}
              step={0.5}
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>

          <div>
            <FieldLabel>Transition</FieldLabel>
            <div className="flex items-center gap-2">
              {TRANSITION_TYPES.map((t) => (
                <button
                  key={t.value}
                  onClick={() => setTransition(t.value)}
                  className={[
                    'flex-1 py-2 rounded-lg text-sm transition-colors',
                    transition === t.value
                      ? 'bg-blue-600 text-white font-medium'
                      : 'bg-gray-800 text-gray-400 hover:text-gray-200',
                  ].join(' ')}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-700">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors">
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
});

export default ShotMetadataEditor;
