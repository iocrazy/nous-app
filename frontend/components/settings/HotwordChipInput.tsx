import React, { useState } from 'react';
import { X } from 'lucide-react';

/**
 * Chip-style editor for the ASR hotword list.
 *
 * The stored value stays the same comma-separated string the backend already
 * consumes (ai_provider_helpers._extract_transcription_hotwords splits on
 * comma/newline) — this component only changes how it is edited: one chip per
 * word with its own remove button, instead of a free-form textarea.
 */
interface HotwordChipInputProps {
  /** Comma- or newline-separated hotword string as stored in ai_settings. */
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export const parseHotwords = (value: string): string[] => {
  const seen = new Set<string>();
  const words: string[] = [];
  for (const raw of value.split(/[,\n]/)) {
    const word = raw.trim();
    const key = word.toLowerCase();
    if (!word || seen.has(key)) continue;
    seen.add(key);
    words.push(word);
  }
  return words;
};

export const HotwordChipInput: React.FC<HotwordChipInputProps> = ({
  value,
  onChange,
  disabled = false,
  placeholder,
}) => {
  const [draft, setDraft] = useState('');
  const words = parseHotwords(value);

  const commit = (raw: string) => {
    const next = parseHotwords(`${words.join(',')},${raw}`);
    if (next.length !== words.length) onChange(next.join(', '));
    setDraft('');
  };

  const removeAt = (index: number) => {
    onChange(words.filter((_, i) => i !== index).join(', '));
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commit(draft);
    } else if (e.key === 'Backspace' && draft === '' && words.length > 0) {
      removeAt(words.length - 1);
    }
  };

  return (
    <div
      className={`w-full bg-ink-950 border border-ink-800 rounded-lg px-2 py-1.5 flex flex-wrap items-center gap-1.5 focus-within:border-indigo-500/60 ${
        disabled ? 'opacity-50 pointer-events-none' : ''
      }`}
    >
      {words.map((word, i) => (
        <span
          key={word.toLowerCase()}
          className="inline-flex items-center gap-1 bg-ink-800 border border-ink-700 rounded-full pl-2.5 pr-1 py-0.5 text-sm text-ink-200"
        >
          {word}
          <button
            type="button"
            aria-label={`Remove ${word}`}
            onClick={() => removeAt(i)}
            disabled={disabled}
            className="p-0.5 rounded-full text-ink-500 hover:text-ink-200 hover:bg-ink-700 transition-colors"
          >
            <X size={12} />
          </button>
        </span>
      ))}
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => draft.trim() && commit(draft)}
        disabled={disabled}
        placeholder={words.length === 0 ? placeholder : undefined}
        className="flex-1 min-w-[120px] bg-transparent px-1 py-0.5 text-sm text-ink-200 placeholder:text-ink-600 focus:outline-none"
      />
    </div>
  );
};
