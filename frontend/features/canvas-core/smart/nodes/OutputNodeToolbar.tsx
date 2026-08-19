// features/canvas-core/smart/nodes/OutputNodeToolbar.tsx
//
// Floating node toolbar (P2-3, small version of Infinite's
// smartNodeToolbarHtml — three keys instead of seven): Preview opens the
// lightbox with ZERO delay (bypassing the 250ms crop-disambiguation
// timer), Download saves the first item, Rerun re-runs the source prompt.
// Positioned absolute above the node — NEVER position:fixed here (RF's
// transformed ancestors collapse fixed into the node, known trap).

import {
  Crop,
  Download,
  Expand,
  Eye,
  Grid3x3,
  Paintbrush,
  RefreshCw,
} from 'lucide-react';
import { useState } from 'react';

import { downloadUrl, type DownloadableItem } from '../downloadMedia';

export interface OutputNodeToolbarProps {
  items: DownloadableItem[];
  onPreview: () => void;
  /** Editing keys (IC 裁剪/扩图/遮罩/宫格切分) — absent → key hidden.
   *  IC's 画笔 shares the Mask editor (brush tool inside), and 放大 has no
   *  backend derive yet, so those two collapse into Mask / stay out. */
  onCrop?: () => void;
  onExpand?: () => void;
  onMask?: () => void;
  onSplit?: () => void;
  /** Absent → no source prompt → the Rerun key is hidden. */
  onRerun?: () => void;
  rerunning?: boolean;
  /** Read-only session: Rerun is disabled (it dispatches a generation and
   *  writes the result back). Preview and Download are pure reads and stay
   *  available — withholding them would take away viewing, not writing. */
  readOnly?: boolean;
  /** Pin visible (the node is selected); otherwise hover reveals. */
  pinned?: boolean;
}

export function OutputNodeToolbar({
  items,
  onPreview,
  onCrop,
  onExpand,
  onMask,
  onSplit,
  onRerun,
  rerunning,
  readOnly,
  pinned,
}: OutputNodeToolbarProps) {
  const [downloadError, setDownloadError] = useState(false);
  if (items.length === 0) return null;

  const onDownload = () => {
    setDownloadError(false);
    void (async () => {
      try {
        await downloadUrl(items[0], 0);
      } catch (err) {
        console.error('toolbar download failed', err);
        setDownloadError(true);
      }
    })();
  };

  return (
    <div
      data-testid="output-node-toolbar"
      className={`canvas-island absolute -top-10 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-xl px-1.5 py-1 transition-opacity duration-150 ${
        pinned ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
      }`}
    >
      <ToolbarButton label="Preview" onClick={onPreview}>
        <Eye size={13} />
      </ToolbarButton>
      {onCrop && (
        <ToolbarButton label="Crop" onClick={onCrop} disabled={readOnly}>
          <Crop size={13} />
        </ToolbarButton>
      )}
      {onExpand && (
        <ToolbarButton label="Expand" onClick={onExpand} disabled={readOnly}>
          <Expand size={13} />
        </ToolbarButton>
      )}
      {onMask && (
        <ToolbarButton label="Mask" onClick={onMask} disabled={readOnly}>
          <Paintbrush size={13} />
        </ToolbarButton>
      )}
      {onSplit && (
        <ToolbarButton label="Split" onClick={onSplit} disabled={readOnly}>
          <Grid3x3 size={13} />
        </ToolbarButton>
      )}
      <ToolbarButton
        label="Download"
        onClick={onDownload}
        tone={downloadError ? 'error' : undefined}
      >
        <Download size={13} />
      </ToolbarButton>
      {onRerun && (
        <ToolbarButton
          label="Rerun"
          onClick={onRerun}
          disabled={rerunning || readOnly}
        >
          <RefreshCw size={13} className={rerunning ? 'animate-spin' : undefined} />
        </ToolbarButton>
      )}
    </div>
  );
}

function ToolbarButton({
  label,
  onClick,
  children,
  disabled,
  tone,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  tone?: 'error';
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={tone === 'error' ? `${label} failed — try again` : label}
      onClick={onClick}
      disabled={disabled}
      className={`nodrag flex h-6 items-center gap-1 rounded-lg px-1.5 text-[10px] font-semibold transition-transform hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-50 ${
        tone === 'error' ? 'text-rose-400' : 'text-canvas-text'
      }`}
    >
      {children}
    </button>
  );
}
