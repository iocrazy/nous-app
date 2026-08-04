// frontend/components/layout/SecondarySidebarHeader.tsx
// The module title that sits at the top of a secondary sidebar rail. Resources,
// Projects and Distribution had each written the same three classes by hand
// (with Resources drifting onto a different colour token), and AI Library had
// no title at all — its first row was a nav item, so the rail read as if it
// belonged to whatever module came before it.
//   title     text-lg font-semibold text-ink-100
//   container px-4 pt-4 pb-3
// The module name is the PRIMARY title on screen: it outranks the content-area
// page title next to it, which PageHeader renders one step down at text-base.
// This deliberately matches PageHeader's `level="module"` classes character for
// character — rail-less modules (Issues, Inspiration, Canvas) render their
// module name through PageHeader instead, and the two must look identical.
// `trailing` is for in-header controls such as the Projects collapse button.

import React from 'react';

export interface SecondarySidebarHeaderProps {
  title: React.ReactNode;
  /** Right-aligned slot inside the header row (collapse button, count…). */
  trailing?: React.ReactNode;
  className?: string;
}

export const SecondarySidebarHeader: React.FC<SecondarySidebarHeaderProps> = ({
  title,
  trailing,
  className = '',
}) => (
  <div className={`flex items-center justify-between px-4 pt-4 pb-3 ${className}`}>
    <span className="text-lg font-semibold text-ink-100">{title}</span>
    {trailing}
  </div>
);

export default SecondarySidebarHeader;
