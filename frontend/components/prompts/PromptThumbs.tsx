import React from 'react';
import { ImageOff } from 'lucide-react';
import { type PromptThumb } from '../../services/promptsService';
import { usePromptThumbSrc } from './usePromptThumbSrc';

export function PromptThumbs({
  thumbs, count, size = 64, testId,
}: { thumbs: PromptThumb[]; count?: number; size?: number; testId?: string }): React.ReactElement {
  const src = usePromptThumbSrc();
  const shown = thumbs.slice(0, 3);
  if (shown.length === 0) {
    return (
      <div
        data-testid={testId}
        data-empty="true"
        style={{ width: size, height: size }}
        className="flex shrink-0 items-center justify-center rounded-md border border-dashed border-line text-content-3"
      >
        <ImageOff size={Math.max(12, size / 4)} />
      </div>
    );
  }
  if (shown.length === 1) {
    return (
      <img data-testid={testId} src={src(shown[0].url)} alt="Prompt thumbnail" style={{ width: size, height: size }} className="shrink-0 rounded-md object-cover" />
    );
  }
  return (
    <div data-testid={testId} className="relative shrink-0" style={{ width: size, height: size }}>
      {shown.map((t, i) => (
        <img
          key={t.url}
          src={src(t.url)}
          alt="Prompt thumbnail"
          className="absolute inset-0 rounded-md object-cover"
          style={{ width: size, height: size, transform: `translate(${i * 3}px, ${-i * 3}px)`, zIndex: shown.length - i, opacity: 1 - i * 0.15 }}
        />
      ))}
      {count !== undefined && (
        <span className="absolute -bottom-1 -right-1 z-10 rounded-full bg-content px-1.5 text-[9px] font-semibold leading-4 text-card">{count}</span>
      )}
    </div>
  );
}
