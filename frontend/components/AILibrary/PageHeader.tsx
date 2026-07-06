// frontend/components/AILibrary/PageHeader.tsx
// THE page-title spec for AI Library pages. Before this, three specs
// coexisted (text-base/ink-200 on the new pages, text-lg/ink-100 on
// Workforce/Usage, a 15px one-off in SkillList) with drifting paddings —
// exactly the "主副标题大小不一致、位置不一致" report. One component,
// one scale:
//   title      text-lg  font-semibold text-ink-100
//   subtitle   text-sm  text-ink-500
//   count      text-xs  text-ink-600 (inline after the title)
//   container  pt-6 pb-4, actions right-aligned and centered
// Sibling rails/columns use their own smaller in-rail headers — this is
// for the PAGE title only.

import React from 'react';

interface PageHeaderProps {
  title: React.ReactNode;
  /** Small count bubble rendered inline after the title. */
  count?: number;
  subtitle?: React.ReactNode;
  /** Right-aligned slot (buttons, status text). */
  actions?: React.ReactNode;
  className?: string;
}

export const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  count,
  subtitle,
  actions,
  className = '',
}) => (
  <div className={`flex items-start justify-between gap-4 pt-6 pb-4 ${className}`}>
    <div className="min-w-0">
      <h1 className="flex items-baseline gap-2 text-lg font-semibold text-ink-100">
        <span className="truncate">{title}</span>
        {count != null && <span className="text-xs font-normal text-ink-600">{count}</span>}
      </h1>
      {subtitle && <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p>}
    </div>
    {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
  </div>
);

export default PageHeader;
