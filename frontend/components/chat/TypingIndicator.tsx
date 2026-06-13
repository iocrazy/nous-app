import React from 'react';

const DOT_DELAYS = ['0ms', '150ms', '300ms'] as const;

export function TypingIndicator(): React.ReactElement {
  return (
    <div className="flex items-center gap-1 px-3 py-2">
      {DOT_DELAYS.map((delay) => (
        <span
          key={delay}
          className="w-1.5 h-1.5 rounded-full bg-ink-500 animate-bounce"
          style={{ animationDelay: delay, animationDuration: '0.8s' }}
        />
      ))}
    </div>
  );
}
