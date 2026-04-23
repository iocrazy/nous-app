import type { ReactNode } from 'react';

interface SidebarSectionProps {
  label: string;
  children: ReactNode;
  /** Hide label (used when parent sidebar is collapsed to w-20). */
  hideLabel?: boolean;
}

export const SidebarSection: React.FC<SidebarSectionProps> = ({
  label,
  children,
  hideLabel = false,
}) => (
  <div>
    {!hideLabel && (
      <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
        {label}
      </div>
    )}
    <div className="flex flex-col gap-1 mt-1">{children}</div>
  </div>
);

export default SidebarSection;
