// frontend/components/AILibrary/MarkdownEditor.tsx
// Minimal textarea wrapper for markdown fields in the AI Library editor.
// Styling follows the surrounding dark theme (zinc-950 surfaces, zinc-800 borders).

import React from 'react';

interface MarkdownEditorProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
  rows?: number;
}

export const MarkdownEditor: React.FC<MarkdownEditorProps> = ({
  value,
  onChange,
  placeholder,
  disabled = false,
  rows = 10,
}) => {
  return (
    <textarea
      className="w-full rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      rows={rows}
    />
  );
};

export default MarkdownEditor;
