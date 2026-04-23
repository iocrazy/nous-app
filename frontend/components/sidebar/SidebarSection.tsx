import type { ReactNode } from 'react';

interface SidebarSectionProps {
  label: string;
  children: ReactNode;
  /** Hide label (used when parent sidebar is collapsed to w-20). */
  hideLabel?: boolean;
  /** Optional trailing action (e.g., a + button) rendered inline with the label. */
  action?: ReactNode;
}

export const SidebarSection: React.FC<SidebarSectionProps> = ({
  label,
  children,
  hideLabel = false,
  action,
}) => (
  <div>
    {!hideLabel && (
      <div className="flex items-center justify-between px-3 py-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
          {label}
        </span>
        {action}
      </div>
    )}
    <div className="flex flex-col gap-1 mt-1">{children}</div>
  </div>
);

export default SidebarSection;
