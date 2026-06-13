// frontend/components/AILibrary/MarkdownBody.tsx
// Renders markdown for skill previews in the AI Library split-pane UI.
// Paperclip's SkillPane uses a MarkdownBody component with the same job —
// take raw .md content, return styled HTML.  We wrap react-markdown with
// remark-gfm + remark-breaks (same plugins already used in the storyboard
// TextAnnotation node, so bundle size stays constant) and a tailwind prose
// theme that matches the rest of the AI Library UI (zinc dark surface +
// indigo accents).

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';

interface MarkdownBodyProps {
  source: string;
  /** Optional className merged onto the wrapper. */
  className?: string;
}

export const MarkdownBody: React.FC<MarkdownBodyProps> = ({
  source,
  className = '',
}) => {
  // The prose classes are hand-picked for the dark AI Library panel —
  // we avoid @tailwindcss/typography's `prose-invert` because it pulls
  // in font sizes that fight the rest of the editor chrome.
  return (
    <div
      className={`text-[14px] leading-relaxed text-ink-200 ${className}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          h1: ({ children }) => (
            <h1 className="mt-6 mb-3 text-2xl font-bold text-ink-100 first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mt-5 mb-2 text-xl font-semibold text-ink-100">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mt-4 mb-2 text-lg font-semibold text-ink-100">
              {children}
            </h3>
          ),
          p: ({ children }) => (
            <p className="my-3 text-ink-300">{children}</p>
          ),
          ul: ({ children }) => (
            <ul className="my-3 ml-6 list-disc space-y-1 text-ink-300 marker:text-ink-500">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="my-3 ml-6 list-decimal space-y-1 text-ink-300 marker:text-ink-500">
              {children}
            </ol>
          ),
          li: ({ children }) => <li className="my-0.5">{children}</li>,
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-400 underline-offset-2 hover:underline"
            >
              {children}
            </a>
          ),
          code: ({ className: cls, children, ...rest }) => {
            const inline = !/language-/.test(cls ?? '');
            if (inline) {
              return (
                <code
                  className="rounded bg-ink-800 px-1.5 py-0.5 text-[13px] font-mono text-amber-300"
                  {...rest}
                >
                  {children}
                </code>
              );
            }
            return (
              <code className={cls} {...rest}>
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            <pre className="my-3 overflow-x-auto rounded-lg border border-ink-800 bg-ink-950 p-3 text-[13px] leading-relaxed text-ink-200">
              {children}
            </pre>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-3 border-l-2 border-ink-700 pl-4 italic text-ink-400">
              {children}
            </blockquote>
          ),
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto">
              <table className="w-full border-collapse text-[13px]">
                {children}
              </table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border-b border-ink-800 px-3 py-1.5 text-left font-semibold text-ink-200">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border-b border-ink-900 px-3 py-1.5 text-ink-300">
              {children}
            </td>
          ),
          hr: () => <hr className="my-6 border-ink-800" />,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
};

export default MarkdownBody;
