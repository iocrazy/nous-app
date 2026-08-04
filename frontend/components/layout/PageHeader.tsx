// frontend/components/layout/PageHeader.tsx
// THE page-title spec for every module's content area. It used to live under
// components/AILibrary/ and only AI Library pages obeyed it, so the six
// business modules each grew their own — a 15px one-off in Inspiration, a
// hardcoded English literal in Issues, hand-written CSS in Distribution.
// That was the reported "各模块页头格式不统一".
//
// TWO title ranks, because the module name outranks the page inside it:
//   module   text-lg   — the module's own name. Rendered by
//                        SecondarySidebarHeader in the rail; modules that have
//                        no rail (Issues, Inspiration, Canvas) render it here
//                        instead, at the SAME size, so the two placements read
//                        as one rank.
//   content  text-base — a page WITHIN a module that already names itself in
//                        its rail (All Projects, Platform Accounts, Agents…).
//                        It must not outshout the module name.
// Shared by both: font-semibold text-ink-100, subtitle text-sm text-ink-500,
// count text-xs text-ink-600 inline after the title, container pt-0 pb-4 (the
// PAGE container owns the top gap), actions right-aligned, tabs on their own
// row underneath.

import React from 'react';

/**
 * 'module'  — this header IS the module's name (module has no secondary rail).
 * 'content' — a page inside a module whose rail already shows the module name.
 */
export type PageHeaderLevel = 'module' | 'content';

export interface PageHeaderProps {
  title: React.ReactNode;
  /**
   * Title rank. Defaults to 'content' because most pages live inside a module
   * that names itself in its rail; only rail-less modules pass 'module'.
   */
  level?: PageHeaderLevel;
  /** Small count rendered inline after the title. */
  count?: number;
  subtitle?: React.ReactNode;
  /** Right-aligned slot (buttons, status text). */
  actions?: React.ReactNode;
  /** Tab strip; gets its own underlined row beneath the title. */
  tabs?: React.ReactNode;
  className?: string;
}

export const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  level = 'content',
  count,
  subtitle,
  actions,
  tabs,
  className = '',
}) => {
  const titleSize = level === 'module' ? 'text-lg' : 'text-base';
  // Without tabs the header IS the root, so className keeps controlling its
  // padding the way every existing caller expects. With tabs it has to wrap.
  const header = (
    <div className={`flex items-start justify-between gap-4 pb-4 ${tabs ? '' : className}`}>
      <div className="min-w-0">
        <h1 className={`flex items-baseline gap-2 ${titleSize} font-semibold text-ink-100`}>
          <span className="truncate">{title}</span>
          {count != null && <span className="text-xs font-normal text-ink-600">{count}</span>}
        </h1>
        {subtitle && <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );

  if (!tabs) return header;

  return (
    <div className={className}>
      {header}
      <div className="flex items-center gap-1 border-b border-ink-800/60 text-[13px]">{tabs}</div>
    </div>
  );
};

export default PageHeader;
