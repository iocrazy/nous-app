import React from 'react';
import { ChevronRight } from 'lucide-react';

export interface BreadcrumbSegment {
  label: string;
  onClick?: () => void;
}

interface BreadcrumbProps {
  segments: BreadcrumbSegment[];
}

export const Breadcrumb: React.FC<BreadcrumbProps> = ({ segments }) => {
  if (segments.length === 0) return null;

  return (
    <nav className="flex items-center gap-1 text-sm text-zinc-400">
      {segments.map((segment, idx) => {
        const isLast = idx === segments.length - 1;
        return (
          <React.Fragment key={idx}>
            {idx > 0 && <ChevronRight size={12} className="text-zinc-600 shrink-0" />}
            {isLast ? (
              <span className="text-zinc-200 font-medium truncate">{segment.label}</span>
            ) : (
              <button
                onClick={segment.onClick}
                className="hover:text-zinc-200 transition-colors truncate cursor-pointer"
              >
                {segment.label}
              </button>
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
};
