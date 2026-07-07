// frontend/components/Inspiration/NoteMarkdown.tsx
// Notes-only rich markdown renderer (spec §2.2 #5b). Adds syntax-highlighted
// code (rehype-highlight) and interactive task-list checkboxes on top of the
// GFM tables/strikethrough react-markdown already gives us. Kept separate from
// the shared AILibrary/MarkdownBody so AI-chat rendering is untouched.
import React, { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/github-dark.css';
import { computeTaskOffsets } from './taskMarkers';

interface Props {
  source: string;
  /** When given, task-list checkboxes become clickable and report their
   *  document-order index; without it they render read-only. */
  onToggleTask?: (index: number) => void;
}

/** Find which task-offset (from computeTaskOffsets, ascending) falls inside
 *  a `li` node's [start, end) source range. Used to derive a checkbox's
 *  document-order index from where it sits in *source*, not from render
 *  order/count — see the StrictMode comment below for why that matters. */
function findTaskIndexInRange(offsets: number[], start: number, end: number): number | null {
  for (let i = 0; i < offsets.length; i += 1) {
    if (offsets[i] >= start && offsets[i] < end) return i;
  }
  return null;
}

export const NoteMarkdown: React.FC<Props> = ({ source, onToggleTask }) => {
  // Each task checkbox's document-order index is derived purely from the
  // *source* — never from a mutable render-time counter. A per-render
  // `useRef` counter (incremented once per `input` renderer invocation) used
  // to work, but broke under React.StrictMode: StrictMode double-invokes
  // each child component's function to surface impure renders, and the
  // `input` renderer is its own fiber, so its second invocation kept
  // incrementing the same shared counter instead of re-deriving the same
  // value — indices came out as [1, 3, 5] instead of [0, 1, 2]. Deriving the
  // index from `node.position` (attached by remark/unified to the `li`, not
  // to the synthetic `input` element GFM injects — verified by inspection)
  // is a pure function of (source, node), so it's stable no matter how many
  // times, or in what order, a given fiber is invoked.
  const taskOffsets = useMemo(() => computeTaskOffsets(source), [source]);

  return (
    <div className="text-[13.5px] leading-relaxed text-content markdown-note">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          input: ({ type, checked, taskIndex, ...rest }: any) => {
            if (type !== 'checkbox') return <input type={type} {...rest} />;
            return (
              <input
                type="checkbox"
                checked={!!checked}
                disabled={!onToggleTask || taskIndex == null}
                onChange={() => {
                  if (taskIndex != null) onToggleTask?.(taskIndex);
                }}
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
          li: ({ className, children, node }: any) => {
            if (!className?.includes('task-list-item')) {
              return <li className={className}>{children}</li>;
            }
            // Derive this item's checkbox index from where it sits in
            // source, then hand it to the `input` child as a plain prop —
            // pure and StrictMode-safe (see NoteMarkdown's top comment).
            const start = node?.position?.start?.offset;
            const end = node?.position?.end?.offset;
            const taskIndex =
              start != null && end != null ? findTaskIndexInRange(taskOffsets, start, end) : null;
            const patchedChildren = React.Children.map(children, (child) =>
              React.isValidElement(child) && (child.props as any)?.type === 'checkbox'
                ? React.cloneElement(child as React.ReactElement<any>, { taskIndex })
                : child,
            );
            return <li className="list-none">{patchedChildren}</li>;
          },
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
