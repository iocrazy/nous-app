// frontend/components/resources/filter/FilterConfigPanel.tsx
//
// "Filter Settings" popover. Shows pinned chips with reorder + unpin
// controls and available chips with a pin button. Closes on outside
// click / Esc; mutations flow back through the useFilterBarConfig
// hook so changes persist immediately.

import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowDown, ArrowUp, Minus, Plus } from 'lucide-react';

import type { ChipId } from './types';

export interface FilterConfigPanelProps {
  pinnedChips: ChipId[];
  availableChips: ChipId[];
  chipLabel: (id: ChipId) => string;
  onPin: (id: ChipId) => void;
  onUnpin: (id: ChipId) => void;
  onReorder: (id: ChipId, direction: 'up' | 'down') => void;
  onClose: () => void;
}

export const FilterConfigPanel: React.FC<FilterConfigPanelProps> = ({
  pinnedChips,
  availableChips,
  chipLabel,
  onPin,
  onUnpin,
  onReorder,
  onClose,
}) => {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
    };
  }, [onClose]);

  return (
    <div
      ref={panelRef}
      className="absolute right-0 top-full mt-1.5 z-40 w-64 bg-zinc-900/95 backdrop-blur-sm border border-zinc-700/80 rounded-xl shadow-2xl py-2 animate-dropdown"
      role="dialog"
      aria-label={t('resources.filter.filterConfig', 'Filter Settings')}
    >
      <div className="px-3 pt-1 pb-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
        {t('resources.filter.pinned', 'Pinned')}
      </div>
      {pinnedChips.length === 0 && (
        <div className="px-3 py-2 text-xs text-zinc-500">
          {t('resources.filter.noneYet', 'None yet')}
        </div>
      )}
      {pinnedChips.map((id, idx) => {
        const canMoveUp = idx > 0;
        const canMoveDown = idx < pinnedChips.length - 1;
        return (
          <div
            key={id}
            className="flex items-center gap-1 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800/60 rounded-md mx-1"
          >
            <span className="flex-1 truncate px-1">{chipLabel(id)}</span>
            <button
              type="button"
              onClick={() => onReorder(id, 'up')}
              disabled={!canMoveUp}
              aria-label={t('resources.filter.moveUp', 'Move up')}
              className="p-1 text-zinc-400 hover:text-zinc-100 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ArrowUp size={12} />
            </button>
            <button
              type="button"
              onClick={() => onReorder(id, 'down')}
              disabled={!canMoveDown}
              aria-label={t('resources.filter.moveDown', 'Move down')}
              className="p-1 text-zinc-400 hover:text-zinc-100 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ArrowDown size={12} />
            </button>
            <button
              type="button"
              onClick={() => onUnpin(id)}
              aria-label={t('resources.filter.unpin', 'Unpin')}
              className="ml-1 p-1 text-zinc-400 hover:text-rose-300"
              title={t('resources.filter.unpin', 'Unpin')}
            >
              <Minus size={12} />
            </button>
          </div>
        );
      })}

      <div className="mx-2.5 my-2 border-t border-zinc-700/60" />

      <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
        {t('resources.filter.available', 'Available')}
      </div>
      {availableChips.length === 0 && (
        <div className="px-3 py-2 text-xs text-zinc-500">
          {t('resources.filter.allPinned', 'All filters pinned')}
        </div>
      )}
      {availableChips.map((id) => (
        <div
          key={id}
          className="flex items-center gap-1 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800/60 rounded-md mx-1"
        >
          <span className="flex-1 truncate px-1">{chipLabel(id)}</span>
          <button
            type="button"
            onClick={() => onPin(id)}
            aria-label={t('resources.filter.pin', 'Pin')}
            className="p-1 text-zinc-400 hover:text-indigo-300"
            title={t('resources.filter.pin', 'Pin')}
          >
            <Plus size={12} />
          </button>
        </div>
      ))}
    </div>
  );
};
