import React from 'react';
import { useTranslation } from 'react-i18next';

const DOT_DELAYS = ['0ms', '150ms', '300ms'] as const;

export interface TypingIndicatorProps {
  /** Display names of users currently typing. Empty array → renders nothing (unless `show`). */
  names?: string[];
  /** Force the bare bouncing dots with no label (e.g. "AI is generating"). */
  show?: boolean;
}

export function TypingIndicator({
  names = [],
  show = false,
}: TypingIndicatorProps): React.ReactElement | null {
  const { t } = useTranslation();

  if (names.length === 0 && !show) return null;

  let label = '';
  if (names.length === 1) {
    label = t('chat.typing.one', { name: names[0] });
  } else if (names.length === 2) {
    label = t('chat.typing.two', { a: names[0], b: names[1] });
  } else if (names.length >= 3) {
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
      {label && <span className="text-[12px] text-content-3 ml-1">{label}</span>}
    </div>
  );
}
