// features/canvas-core/smart/nodes/GroupNodeToolbar.tsx
//
// IC-parity floating toolbar for the group node (Infinite's
// smartGroupToolbarHtml: 整理排列 / 预览 / 宫格拼接 / 批量下载 / 解散分组).
// Same island shell + hover/pin reveal as OutputNodeToolbar. Enablement
// copies IC exactly: Arrange needs content, Preview/Download need ≥1
// image, Stitch needs ≥2, Ungroup is unconditional. Writes (arrange /
// stitch / ungroup) are withheld read-only; preview/download are reads.

import {
  Archive,
  Eye,
  Grid3x3,
  LayoutGrid,
  Ungroup as UngroupIcon,
} from 'lucide-react';

export interface GroupNodeToolbarProps {
  imageCount: number;
  memberCount: number;
  onArrange: () => void;
  onPreview: () => void;
  onStitch: () => void;
  onDownload: () => void;
  onUngroup: () => void;
  readOnly?: boolean;
  /** Pin visible (the node is selected); otherwise hover reveals. */
  pinned?: boolean;
  /**
   * Pointer over the group card, or focus inside it (fluency Wave 2, Task 5).
   * Unpinned and unhovered the bar is NOT RENDERED — same reasoning as
   * OutputNodeToolbar: a frosted island faded to `opacity-0` still repaints.
   */
  hovered?: boolean;
}

export function GroupNodeToolbar({
  imageCount,
  memberCount,
  onArrange,
  onPreview,
  onStitch,
  onDownload,
  onUngroup,
  readOnly,
  pinned,
  hovered,
}: GroupNodeToolbarProps) {
  const hasContent = imageCount > 0 || memberCount > 0;
  // Mount gate (T5) — see OutputNodeToolbar.
  if (!pinned && !hovered) return null;
  return (
    <div
      data-testid="group-node-toolbar"
      className={`canvas-island absolute -top-10 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-xl px-1.5 py-1 transition-opacity duration-150 ${
        pinned ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
      }`}
    >
      {/* IC shows icon+text on every key (整理排列/预览/宫格拼接/批量下载/
          解散分组) — text visible, not tooltip-only. */}
      <BarButton label="Arrange" onClick={onArrange} disabled={readOnly || !hasContent}>
        <LayoutGrid size={13} />
        <span>Arrange</span>
      </BarButton>
      <BarButton label="Preview" onClick={onPreview} disabled={imageCount < 1}>
        <Eye size={13} />
        <span>Preview</span>
      </BarButton>
      <BarButton label="Stitch" onClick={onStitch} disabled={readOnly || imageCount < 2}>
        <Grid3x3 size={13} />
        <span>Stitch</span>
      </BarButton>
      <BarButton label="Download" onClick={onDownload} disabled={imageCount < 1}>
        <Archive size={13} />
        <span>Download</span>
      </BarButton>
      <BarButton label="Ungroup" onClick={onUngroup} disabled={readOnly}>
        <UngroupIcon size={13} />
        <span>Ungroup</span>
      </BarButton>
    </div>
  );
}

function BarButton({
  label,
  onClick,
  children,
  disabled,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className="nodrag flex h-6 items-center gap-1 rounded-lg px-1.5 text-[10px] font-semibold text-canvas-text transition-transform hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  );
}
