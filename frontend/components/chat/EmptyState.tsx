import React, { useCallback } from 'react';
import { Sparkles } from 'lucide-react';

const SUGGESTIONS = [
  'Help me outline a story',
  'Review my chapter',
  'Improve dialogue',
  'Suggest plot ideas',
] as const;

export interface EmptyStateProps {
  onSuggest?: (suggestion: string) => void;
}

export function EmptyState({ onSuggest }: EmptyStateProps): React.ReactElement {
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
        <h3 className="text-base font-medium text-zinc-200">Start a conversation</h3>
        <p className="mt-1 text-sm text-zinc-500">
          Ask your AI assistant anything about your project.
        </p>
      </div>

      <div className="flex flex-col gap-2 w-full max-w-xs">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            onClick={() => handleClick(suggestion)}
            className="
              border border-zinc-700 hover:bg-zinc-800
              rounded-lg px-3 py-2
              text-sm text-zinc-400 hover:text-zinc-200
              transition-colors text-left
            "
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  );
}
