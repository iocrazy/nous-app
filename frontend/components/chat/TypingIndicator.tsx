import React from 'react';
import { useTranslation } from 'react-i18next';

const DOT_DELAYS = ['0ms', '150ms', '300ms'] as const;

export interface TypingIndicatorProps {
  /** Display names of users currently typing. Empty array → renders nothing. */
  names?: string[];
}

export function TypingIndicator({
  names = [],
}: TypingIndicatorProps): React.ReactElement | null {
  const { t } = useTranslation();

  if (names.length === 0) return null;

  let label: string;
  if (names.length === 1) {
    label = t('chat.typing.one', { name: names[0] });
  } else if (names.length === 2) {
    label = t('chat.typing.two', { a: names[0], b: names[1] });
  } else {
    label = t('chat.typing.many');
  }

  return (
    <div className="flex items-center gap-1 px-[18px] py-[6px]">
      {DOT_DELAYS.map((delay) => (
        <span
          key={delay}
          className="w-1.5 h-1.5 rounded-full bg-ink-500 animate-bounce"
          style={{ animationDelay: delay, animationDuration: '0.8s' }}
        />
      ))}
      <span className="text-[12px] text-[#74747e] ml-1">{label}</span>
    </div>
  );
}
