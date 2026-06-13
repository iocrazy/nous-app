import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles } from 'lucide-react';

const SUGGESTION_KEYS = [
  'chat.suggestions.outlineStory',
  'chat.suggestions.reviewChapter',
  'chat.suggestions.improveDialogue',
  'chat.suggestions.suggestPlot',
] as const;

export interface EmptyStateProps {
  onSuggest?: (suggestion: string) => void;
}

export function EmptyState({ onSuggest }: EmptyStateProps): React.ReactElement {
  const { t } = useTranslation();

  const handleClick = useCallback(
    (suggestion: string) => {
      onSuggest?.(suggestion);
    },
    [onSuggest],
  );

  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 px-6 py-10 text-center">
      <Sparkles size={32} className="text-indigo-400" />

      <div>
        <h3 className="text-base font-medium text-ink-200">{t('chat.emptyTitle')}</h3>
        <p className="mt-1 text-sm text-ink-500">
          {t('chat.emptySubtitle')}
        </p>
      </div>

      <div className="flex flex-col gap-2 w-full max-w-xs">
        {SUGGESTION_KEYS.map((key) => {
          const label = t(key);
          return (
            <button
              key={key}
              type="button"
              onClick={() => handleClick(label)}
              className="
                border border-ink-700 hover:bg-ink-800
                rounded-lg px-3 py-2
                text-sm text-ink-400 hover:text-ink-200
                transition-colors text-left
              "
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
