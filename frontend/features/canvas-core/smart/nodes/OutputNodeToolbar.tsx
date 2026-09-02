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
  Boxes,
  Expand,
  Eye,
  Grid3x3,
  Paintbrush,
  RefreshCw,
  Theater,
  Maximize2,
  Copy,
} from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

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
  /** IC 画笔 — annotate with free/rect/ellipse/label/text shapes. */
  onBrush?: () => void;
  /** IC 放大 — jimeng image_upscale on the current image. */
  onUpscale?: () => void;
  /** IC duplicateSmartNodeMediaToCanvas — copy this image out as its own node. */
  onDuplicate?: () => void;
  upscaling?: boolean;
  onSplit?: () => void;
  /** Absent → no source prompt → the Rerun key is hidden. */
  onRerun?: () => void;
  rerunning?: boolean;
  /**
   * "As Asset…" (P4 Task 6) — promote this output into the asset library.
   *
   * ALWAYS RENDERED WHEN SUPPLIED, EVEN WHEN IT CANNOT RUN. An output whose
   * image predates the `GeneratedImageRef.id` field has no `generated_media`
   * row to promote, and a canvas opened outside a `/team/:teamId` route has no
   * asset scope to promote into — both are real, and in both the honest UI is
   * a disabled key whose tooltip says which. Hiding it instead would leave the
   * user comparing two output nodes and unable to tell why one offers the
   * action and the other does not.
   */
  onAsAsset?: () => void;
  asAssetDisabled?: boolean;
  /** Replaces the tooltip while disabled — the REASON, not the label. */
  asAssetDisabledReason?: string;
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
  onBrush,
  onSplit,
  onUpscale,
  upscaling,
  onDuplicate,
  onRerun,
  rerunning,
  onAsAsset,
  asAssetDisabled,
  asAssetDisabledReason,
  readOnly,
  pinned,
}: OutputNodeToolbarProps) {
  const { t } = useTranslation();
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
          <Theater size={13} />
        </ToolbarButton>
      )}
      {onBrush && (
        <ToolbarButton label="Brush" onClick={onBrush} disabled={readOnly}>
          <Paintbrush size={13} />
        </ToolbarButton>
      )}
      {onSplit && (
        <ToolbarButton label="Split" onClick={onSplit} disabled={readOnly}>
          <Grid3x3 size={13} />
        </ToolbarButton>
      )}
      {onUpscale && (
        <ToolbarButton
          label="Upscale"
          onClick={onUpscale}
          disabled={readOnly || upscaling}
        >
          <Maximize2 size={13} className={upscaling ? 'animate-pulse' : undefined} />
        </ToolbarButton>
      )}
      {onDuplicate && (
        <ToolbarButton label="Copy to canvas" onClick={onDuplicate} disabled={readOnly}>
          <Copy size={13} />
        </ToolbarButton>
      )}
      {onAsAsset && (
        <ToolbarButton
          label={t('canvas.asAsset.action', 'As Asset…')}
          onClick={onAsAsset}
          disabled={readOnly || asAssetDisabled}
          // Both disabled paths say WHY. `asAssetDisabled` covers "not an
          // image" and "no workspace in the URL"; read-only is the third, and
          // leaving it with only the button's own label made a greyed key look
          // like an omission rather than a permission.
          title={
            asAssetDisabled
              ? asAssetDisabledReason
              : readOnly
                // Reuses the canvas's existing read-only label rather than
                // minting a fourth way to say the same thing.
                ? t('canvas.readOnly', 'Read-only')
                : undefined
          }
        >
          <Boxes size={13} />
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
  title,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  tone?: 'error';
  /** Overrides the tooltip. The accessible NAME stays `label` either way, so a
   *  reason never replaces the thing the key is called. */
  title?: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={title ?? (tone === 'error' ? `${label} failed — try again` : label)}
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
