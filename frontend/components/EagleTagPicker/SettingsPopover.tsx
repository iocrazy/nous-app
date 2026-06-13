import React, { useRef, useEffect } from 'react';
import { List, LayoutGrid } from 'lucide-react';
import type { PickerSettings } from '../../services/tagPreferencesService';

interface SettingsPopoverProps {
  settings: PickerSettings;
  onUpdate: (partial: Partial<PickerSettings>) => void;
  onClose: () => void;
}

export const SettingsPopover: React.FC<SettingsPopoverProps> = ({
  settings,
  onUpdate,
  onClose,
}) => {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  const Toggle: React.FC<{ label: string; checked: boolean; disabled?: boolean; onChange: (v: boolean) => void }> = ({
    label, checked, disabled, onChange,
  }) => (
    <div className="flex items-center justify-between py-1">
      <span className={`text-xs ${disabled ? 'text-ink-600' : 'text-ink-300'}`}>{label}</span>
      <button
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`w-8 h-4 rounded-full transition-colors relative ${
          disabled ? 'bg-ink-800 cursor-not-allowed' : checked ? 'bg-indigo-500' : 'bg-ink-700'
        }`}
      >
        <span
          className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
            checked ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full mt-1 z-[70] bg-ink-900 border border-ink-700 rounded-lg shadow-xl w-52 p-3 space-y-2"
    >
      {/* Layout */}
      <div className="flex items-center justify-between py-1">
        <span className="text-xs text-ink-300">Layout</span>
        <div className="flex gap-1">
          <button
            onClick={() => onUpdate({ layout: 'list' })}
            className={`p-1 rounded ${settings.layout === 'list' ? 'bg-ink-700 text-ink-50' : 'text-ink-500'}`}
          >
            <List size={14} />
          </button>
          <button
            onClick={() => onUpdate({ layout: 'grid' })}
            className={`p-1 rounded ${settings.layout === 'grid' ? 'bg-ink-700 text-ink-50' : 'text-ink-500'}`}
          >
            <LayoutGrid size={14} />
          </button>
        </div>
      </div>

      {/* Column Width */}
      <div className="flex items-center justify-between py-1">
        <span className="text-xs text-ink-300">Column Width</span>
        <select
          value={settings.columnWidth}
          onChange={(e) => onUpdate({ columnWidth: e.target.value as any })}
          className="bg-ink-800 border border-ink-700 rounded px-2 py-0.5 text-xs text-ink-300"
        >
          <option value="small">Small</option>
          <option value="medium">Medium</option>
          <option value="large">Large</option>
        </select>
      </div>

      <div className="border-t border-ink-800 my-1" />

      {/* Toggles */}
      <Toggle label="Starred" checked={settings.showStarred} onChange={(v) => onUpdate({ showStarred: v })} />
      <Toggle label="Frequently Used" checked={settings.showRecently} onChange={(v) => onUpdate({ showRecently: v })} />
      <Toggle label="Recommended" checked={settings.showRecommended} disabled onChange={() => {}} />
      <Toggle label="Count" checked={settings.showCount} onChange={(v) => onUpdate({ showCount: v })} />
    </div>
  );
};
