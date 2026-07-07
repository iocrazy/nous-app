// frontend/components/Inspiration/NoteMarkdown.tsx
// Notes-only rich markdown renderer (spec §2.2 #5b). Adds syntax-highlighted
// code (rehype-highlight) and interactive task-list checkboxes on top of the
// GFM tables/strikethrough react-markdown already gives us. Kept separate from
// the shared AILibrary/MarkdownBody so AI-chat rendering is untouched.
import React, { useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/github-dark.css';

interface Props {
  source: string;
  /** When given, task-list checkboxes become clickable and report their
   *  document-order index; without it they render read-only. */
  onToggleTask?: (index: number) => void;
}

export const NoteMarkdown: React.FC<Props> = ({ source, onToggleTask }) => {
  // Assign each task checkbox its document-order index. react-markdown renders
  // synchronously in document order within one pass, so a per-render counter
  // (reset here, incremented as each checkbox renders) yields stable indices
  // matching toggleTaskItem's contract.
  const counter = useRef(0);
  counter.current = 0;

  return (
    <div className="text-[13.5px] leading-relaxed text-content markdown-note">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          input: ({ type, checked, ...rest }) => {
            if (type !== 'checkbox') return <input type={type} {...rest} />;
            const idx = counter.current;
            counter.current += 1;
            return (
              <input
                type="checkbox"
                checked={!!checked}
                disabled={!onToggleTask}
                onChange={() => onToggleTask?.(idx)}
                className="mr-1.5 align-middle accent-indigo-500"
              />
            );
          },
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer" className="text-indigo-300 hover:underline">
              {children}
            </a>
          ),
          pre: ({ children }) => (
            <pre className="my-2 overflow-x-auto rounded-lg bg-island-2 p-3 text-[12px]">{children}</pre>
          ),
          code: ({ className, children, ...rest }) => (
            <code className={`${className ?? ''} rounded bg-island-2 px-1 py-0.5 text-[12px]`} {...rest}>
              {children}
            </code>
          ),
          table: ({ children }) => (
            <div className="my-2 overflow-x-auto">
              <table className="w-full border-collapse text-[12.5px]">{children}</table>
            </div>
          ),
          th: ({ children }) => <th className="border border-line px-2 py-1 text-left font-semibold">{children}</th>,
          td: ({ children }) => <td className="border border-line px-2 py-1">{children}</td>,
          li: ({ className, children }) => (
            <li className={className?.includes('task-list-item') ? 'list-none' : undefined}>{children}</li>
          ),
          ul: ({ children }) => <ul className="my-1 list-disc pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-1 list-decimal pl-5">{children}</ol>,
          p: ({ children }) => <p className="my-1">{children}</p>,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
};
