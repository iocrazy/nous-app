// frontend/components/resources/filter/AIStatusFilterDropdown.tsx
//
// Three independent AI-status checkboxes (AND semantics — every flag
// that's checked must equal "completed" on the resource).

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Eye, FileText, ListTree, type LucideIcon } from 'lucide-react';

import type { AIStatusChipValue } from './types';

export interface AIStatusFilterDropdownProps {
  value: AIStatusChipValue;
  onChange: (next: AIStatusChipValue) => void;
  onClearAll: () => void;
}

type FlagKey = keyof AIStatusChipValue;

const FLAG_ORDER: FlagKey[] = ['transcribed', 'summarized', 'analyzed'];

export const AIStatusFilterDropdown: React.FC<AIStatusFilterDropdownProps> = ({
  value,
  onChange,
  onClearAll,
}) => {
  const { t } = useTranslation();

  const toggle = (key: FlagKey) => {
    onChange({ ...value, [key]: !value[key] });
  };

  const labels: Record<FlagKey, string> = {
    transcribed: t('resources.filter.ai.transcribed', 'Transcribed'),
    summarized: t('resources.filter.ai.summarized', 'Summarized'),
    analyzed: t('resources.filter.ai.analyzed', 'Analyzed'),
  };

  const icons: Record<FlagKey, LucideIcon> = {
    transcribed: FileText,
    summarized: ListTree,
    analyzed: Eye,
  };

  const anyActive = FLAG_ORDER.some((k) => value[k]);

  return (
    <div className="w-48 py-1" role="menu" aria-label="AI status filter">
      {FLAG_ORDER.map((key) => {
        const active = value[key];
        const Icon = icons[key];
        return (
          <button
            key={key}
            type="button"
            onClick={() => toggle(key)}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-colors ${
              active
                ? 'bg-indigo-500/10 text-indigo-300'
                : 'text-ink-300 hover:bg-ink-800'
            }`}
          >
            <span className="flex items-center gap-2">
              <Icon
                size={12}
                className={active ? 'text-indigo-300' : 'text-ink-500'}
                aria-hidden="true"
              />
              <span>{labels[key]}</span>
            </span>
            {active && <Check size={12} className="text-indigo-400" />}
          </button>
        );
      })}
      {anyActive && (
        <>
          <div className="mx-2.5 my-1 border-t border-ink-700/60" />
          <button
            type="button"
            onClick={onClearAll}
            className="w-full text-left px-3 py-2 text-xs text-ink-500 hover:text-ink-300 hover:bg-ink-800 transition-colors"
          >
            {t('resources.filter.clearSelection', 'Clear selection')}
          </button>
        </>
      )}
    </div>
  );
};
