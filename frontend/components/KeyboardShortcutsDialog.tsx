import React, { useEffect, useCallback } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface KeyboardShortcutsDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

interface ShortcutEntry {
  labelKey: string;
  fallback: string;
  keys: string[];
}

interface ShortcutSection {
  titleKey: string;
  titleFallback: string;
  entries: ShortcutEntry[];
}

const playerShortcuts: ShortcutEntry[] = [
  { labelKey: 'shortcuts.playPause', fallback: 'Play / Pause', keys: ['Space'] },
  { labelKey: 'shortcuts.fullscreen', fallback: 'Fullscreen', keys: ['F'] },
  { labelKey: 'shortcuts.mute', fallback: 'Mute', keys: ['M'] },
  { labelKey: 'shortcuts.forward', fallback: 'Forward 5s', keys: ['\u2192'] },
  { labelKey: 'shortcuts.backward', fallback: 'Backward 5s', keys: ['\u2190'] },
  { labelKey: 'shortcuts.nextFrame', fallback: 'Next Frame', keys: ['.'] },
  { labelKey: 'shortcuts.prevFrame', fallback: 'Prev Frame', keys: [','] },
  { labelKey: 'shortcuts.forward10', fallback: '+10 Frames', keys: ['Shift', '.'] },
  { labelKey: 'shortcuts.backward10', fallback: '-10 Frames', keys: ['Shift', ','] },
];

const navigationShortcuts: ShortcutEntry[] = [
  { labelKey: 'shortcuts.prevFile', fallback: 'Prev File', keys: ['\u25C0'] },
  { labelKey: 'shortcuts.nextFile', fallback: 'Next File', keys: ['\u25B6'] },
];

const speedShortcuts: ShortcutEntry[] = [
  { labelKey: 'shortcuts.speedUp', fallback: 'Speed Up', keys: [']'] },
  { labelKey: 'shortcuts.speedDown', fallback: 'Speed Down', keys: ['['] },
  { labelKey: 'shortcuts.resetSpeed', fallback: 'Reset Speed', keys: ['\\'] },
  { labelKey: 'shortcuts.showShortcuts', fallback: 'Show Shortcuts', keys: ['?'] },
];

const leftColumn: ShortcutSection[] = [
  { titleKey: 'shortcuts.player', titleFallback: 'Player', entries: playerShortcuts },
];

const rightColumn: ShortcutSection[] = [
  { titleKey: 'shortcuts.navigation', titleFallback: 'Navigation', entries: navigationShortcuts },
  { titleKey: 'shortcuts.speed', titleFallback: 'Speed', entries: speedShortcuts },
];

const KeyBadge: React.FC<{ label: string }> = ({ label }) => (
  <span className="bg-ink-800 border border-ink-700 rounded px-2 py-0.5 text-xs font-mono text-ink-300">
    {label}
  </span>
);

const ShortcutRow: React.FC<{ entry: ShortcutEntry; t: (key: string, fallback: string) => string }> = ({ entry, t }) => (
  <div className="flex items-center justify-between py-1.5">
    <span className="text-sm text-ink-300">{t(entry.labelKey, entry.fallback)}</span>
    <div className="flex items-center gap-1">
      {entry.keys.map((key, i) => (
        <React.Fragment key={i}>
          {i > 0 && <span className="text-[10px] text-ink-500">+</span>}
          <KeyBadge label={key} />
        </React.Fragment>
      ))}
    </div>
  </div>
);

const ShortcutSectionBlock: React.FC<{ section: ShortcutSection; t: (key: string, fallback: string) => string }> = ({ section, t }) => (
  <div className="mb-5 last:mb-0">
    <h3 className="text-[11px] font-semibold text-ink-500 uppercase tracking-widest mb-3">
      {t(section.titleKey, section.titleFallback)}
    </h3>
    <div className="space-y-0.5">
      {section.entries.map((entry) => (
        <ShortcutRow key={entry.labelKey} entry={entry} t={t} />
      ))}
    </div>
  </div>
);

export const KeyboardShortcutsDialog: React.FC<KeyboardShortcutsDialogProps> = ({
  isOpen,
  onClose,
}) => {
  const { t } = useTranslation();

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    },
    [onClose],
  );

  useEffect(() => {
    if (!isOpen) return;
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-ink-900 border border-ink-800 rounded-2xl shadow-2xl w-full max-w-lg mx-4 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-ink-800">
          <h2 className="text-lg font-semibold text-white">
            {t('shortcuts.title', 'Keyboard Shortcuts')}
          </h2>
          <button
            onClick={onClose}
            className="p-2 text-ink-400 hover:text-white hover:bg-ink-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Body — two columns */}
        <div className="grid grid-cols-2 gap-6 p-5">
          {/* Left column */}
          <div>
            {leftColumn.map((section) => (
              <ShortcutSectionBlock key={section.titleKey} section={section} t={t} />
            ))}
          </div>

          {/* Right column */}
          <div>
            {rightColumn.map((section) => (
              <ShortcutSectionBlock key={section.titleKey} section={section} t={t} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
