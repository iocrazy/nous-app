// features/canvas-core/smart/nodes/GenFooterControls.tsx
//
// IC-parity generation footer (⑨, reference shots 20-23): every knob is a
// PILL that pops an upward panel — model list (selected dot), ratio grid
// with semantic labels (IC 尺寸选择), quality Auto/Low/Medium/High, count
// 1-8 grid. Shared by the prompt node footer and the attached composer so
// the two stay identical. State lives with the caller (patch-style
// onChange); only the open-popover bookkeeping is local.

import {
  Copy,
  Scan,
  SlidersHorizontal,
  Sparkles,
Timer,
  Monitor,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import type { PromptGenSettings } from '../types';

export interface FooterModel {
  name: string;
  display_name?: string;
  /** True when the row runs on the user's own machine via the paired codex
   *  daemon (C 方案). Derived server-side — the raw provider name is behind
   *  the 2026-08-14 leak tripwire and never reaches the client. */
  is_local?: boolean;
}

export interface GenFooterControlsProps {
  gen: PromptGenSettings;
  models: FooterModel[];
  onChange: (patch: Partial<PromptGenSettings>) => void;
  disabled?: boolean;
}

/** Semantic label per ratio (IC 尺寸选择 right-hand hints). */
export const RATIO_LABELS: Record<string, string> = {
  '1:1': 'Square',
  '2:3': 'Portrait',
  '3:2': 'Landscape',
  '3:4': 'Portrait',
  '4:3': 'Landscape',
  '9:16': 'Tall',
  '16:9': 'Wide',
  '21:9': 'Ultrawide',
};

/** Resolution ladder (IC 系统参数 right column). Consumed by providers
 *  with a resolution knob (jimeng resolution_type); codex sizes are fixed
 *  by the model and ignore it. */
/** Video aspect set (IC 画面比例: jimeng-legal ratios + 自适应). */
export const VIDEO_RATIOS: Array<{ value: string; label: string }> = [
  { value: '16:9', label: '16:9' },
  { value: '9:16', label: '9:16' },
  { value: '1:1', label: '1:1' },
  { value: '4:3', label: '4:3' },
  { value: '3:4', label: '3:4' },
  { value: '21:9', label: '21:9' },
  { value: 'auto', label: 'Adaptive' },
];

/** Video resolutions (IC 分辨率: 720p everywhere; 1080p/4k vip models). */
export const VIDEO_RESOLUTIONS = ['720p', '1080p', '4k'] as const;

/** Video reference modes (IC 全能参考/首尾帧). */
export const VIDEO_MODES: Array<{ value: 'multimodal' | 'frames' | undefined; label: string; hint: string }> = [
  { value: undefined, label: 'Auto', hint: 'Single ref i2v / t2v' },
  { value: 'multimodal', label: 'Omni Ref', hint: 'All refs guide the clip' },
  { value: 'frames', label: 'First & Last', hint: 'Refs 1+2 become the end frames' },
];

/** Video clip lengths (IC 时长 pill: 8 quick picks; CLI snaps per model). */
export const DURATIONS = [3, 4, 5, 6, 8, 10, 12, 15] as const;

export const RESOLUTIONS: Array<{ value: string; label: string; hint: string }> = [
  { value: '1k', label: '1K', hint: '1024×1024' },
  { value: '2k', label: '2K', hint: '2048×2048' },
  { value: '4k', label: '4K', hint: '4096×4096' },
];
export const FOOTER_RATIOS = Object.keys(RATIO_LABELS);

const QUALITIES: Array<{ label: string; value: string | undefined }> = [
  { label: 'Auto', value: undefined },
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' },
];

type PopKey = 'model' | 'size' | 'quality' | 'count' | 'duration' | 'vres';

export function GenFooterControls({
  gen,
  models,
  onChange,
  disabled,
}: GenFooterControlsProps) {
  const [open, setOpen] = useState<PopKey | null>(null);
  // Was the open popover summoned by a CLICK (pinned) or by hovering?
  //
  // Both gestures open it — hover is IC parity — but they should not mean the
  // same thing. A hover menu follows the pointer and leaves with it; something
  // the user deliberately clicked should stay put until dismissed.
  //
  // Without this split the two cancel out: a real click is always preceded by
  // mouseenter, so hover opens the popover and the click then toggles it shut.
  // The control looks dead to anyone who clicks instead of hovering, and the
  // only way in — hover — drops the menu again the moment the pointer moves.
  const [pinned, setPinned] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Click-away closes whichever popover is open.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(null);
        setPinned(false);
      }
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [open]);

  /** Click: pin this popover open, or dismiss it if it was already pinned. */
  const toggle = (key: PopKey) => {
    setOpen((cur) => (cur === key && pinned ? null : key));
    setPinned((wasPinned) => !(open === key && wasPinned));
  };
  /** Hover: open, but only while nothing is pinned — a pinned menu must not
   *  be yanked away by the pointer brushing a neighbouring pill. */
  const hoverOpen = (key: PopKey) => {
    if (!pinned) setOpen(key);
  };
  const pick = (patch: Partial<PromptGenSettings>) => {
    onChange(patch);
    setOpen(null);
    setPinned(false);
  };

  const isImage = gen.kind === 'image';
  const ratioValue = (isImage ? gen.ratio : gen.aspect) ?? '1:1';
  const modelLabel =
    models.find((m) => m.name === gen.model)?.display_name ||
    gen.model ||
    'Default';
  const qualityLabel =
    QUALITIES.find((q) => q.value === (gen.quality ?? undefined))?.label ??
    'Auto';

  return (
    <div
      ref={rootRef}
      className="relative flex min-w-0 items-center gap-1"
      onMouseLeave={() => {
        if (!pinned) setOpen(null);
      }}
    >
      <Pill
        testid="pill-model"
        ariaLabel="Generation model"
        onClick={() => toggle('model')}
        onHover={() => hoverOpen('model')}
        disabled={disabled}
        className="min-w-0 flex-1"
      >
        <Sparkles size={11} />
        <span className="truncate">{modelLabel}</span>
      </Pill>
      <Pill
        testid="pill-size"
        ariaLabel="Aspect ratio"
        onClick={() => toggle('size')}
        onHover={() => hoverOpen('size')}
        disabled={disabled}
      >
        <Scan size={11} />
        <span>
          {ratioValue}
          {isImage ? ` · ${(gen.resolution ?? '1k').toUpperCase()}` : ''}
        </span>
      </Pill>
      {!isImage && (
        <Pill
          testid="pill-vres"
          ariaLabel="Video resolution"
          onClick={() => toggle('vres')}
        onHover={() => hoverOpen('vres')}
          disabled={disabled}
        >
          <Monitor size={11} />
          <span>{gen.resolution ? gen.resolution.toUpperCase() : 'Auto'}</span>
        </Pill>
      )}
      {open === 'vres' && !isImage && (
        <Pop title="Video Resolution">
          <div className="flex w-40 flex-col gap-1">
            {[undefined, '720p', '1080p', '4k'].map((r) => (
              <button
                key={r ?? 'auto'}
                type="button"
                data-testid="vres-option"
                onClick={() => pick({ resolution: r } as never)}
                className={`nodrag flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-xs ${
                  gen.resolution === r
                    ? 'border-canvas-strong font-bold text-canvas-text'
                    : 'border-canvas-line text-canvas-text'
                }`}
              >
                <span>{r ? r.toUpperCase() : 'Auto'}</span>
                {gen.resolution === r && <span className="text-canvas-strong">●</span>}
              </button>
            ))}
          </div>
        </Pop>
      )}
      {!isImage && (
        <Pill
          testid="pill-duration"
          ariaLabel="Clip duration"
          onClick={() => toggle('duration')}
        onHover={() => hoverOpen('duration')}
          disabled={disabled}
        >
          <Timer size={11} />
          <span>
            {gen.duration ?? 5}s
            {gen.video_mode === 'multimodal' ? ' · Omni' : gen.video_mode === 'frames' ? ' · F&L' : ''}
          </span>
        </Pill>
      )}
      {isImage && (
        <Pill
          testid="pill-quality"
          ariaLabel="Quality"
          onClick={() => toggle('quality')}
        onHover={() => hoverOpen('quality')}
          disabled={disabled}
        >
          <SlidersHorizontal size={11} />
          <span>{qualityLabel}</span>
        </Pill>
      )}
      {isImage && (
        <Pill
          testid="pill-count"
          ariaLabel="Image count"
          onClick={() => toggle('count')}
        onHover={() => hoverOpen('count')}
          disabled={disabled}
        >
          <Copy size={11} />
          <span>{gen.count ?? 1}</span>
        </Pill>
      )}

      {open === 'model' && (
        <Pop title="Model">
          <div className="flex max-h-56 w-56 flex-col gap-0.5 overflow-y-auto">
            <PopRow
              label="Catalog default"
              active={!gen.model}
              onClick={() => pick({ model: '' })}
            />
            {models.map((m) => (
              <PopRow
                key={m.name}
                label={
                  m.is_local
                    ? `${m.display_name || m.name} · local`
                    : m.display_name || m.name
                }
                active={gen.model === m.name}
                onClick={() => pick({ model: m.name })}
              />
            ))}
          </div>
        </Pop>
      )}
      {open === 'duration' && !isImage && (
        <Pop title="Duration">
          <div className="w-44">
            <div className="grid grid-cols-4 gap-1">
              {DURATIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  data-testid="duration-option"
                  onMouseEnter={() => onChange({ duration: d })}
                  onClick={() => pick({ duration: d })}
                  className={`nodrag rounded-lg border px-1.5 py-1 text-xs ${
                    (gen.duration ?? 5) === d
                      ? 'border-canvas-strong font-bold text-canvas-text'
                      : 'border-canvas-line text-canvas-text'
                  }`}
                >
                  {d}s
                </button>
              ))}
            </div>
            {/* IC custom seconds field (1–60, clamped). */}
            <label className="mt-1.5 flex items-center gap-1.5 text-[10px] text-canvas-muted">
              Custom
              <input
                type="number"
                min={1}
                max={60}
                value={gen.duration ?? 5}
                aria-label="Custom duration seconds"
                onChange={(e) => {
                  const v = Math.min(60, Math.max(1, Math.round(Number(e.target.value) || 5)));
                  onChange({ duration: v });
                }}
                className="nodrag w-14 rounded border border-canvas-line bg-transparent px-1.5 py-0.5 text-xs text-canvas-text outline-none"
              />
              s
            </label>
          </div>
        </Pop>
      )}
      {open === 'size' && (
        <Pop title="Size">
          {/* IC 尺寸选择: roomy two-column panel — ratios left, resolution
              ladder right; HOVER selects (滑动到即选择), click closes. */}
          <div className="flex w-[380px] gap-3">
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              {(isImage
                ? FOOTER_RATIOS.map((r) => ({ value: r, label: RATIO_LABELS[r] ?? '' }))
                : VIDEO_RATIOS.map((r) => ({ value: r.value, label: r.label }))
              ).map(({ value: r, label: rl }) => (
                <button
                  key={r}
                  type="button"
                  data-testid="ratio-option"
                  onMouseEnter={() =>
                    onChange(isImage ? { ratio: r } : { aspect: r })
                  }
                  onClick={() =>
                    pick(isImage ? { ratio: r } : { aspect: r })
                  }
                  className={`nodrag flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-xs ${
                    ratioValue === r
                      ? 'border-canvas-strong font-bold text-canvas-text'
                      : 'border-canvas-line text-canvas-text'
                  }`}
                >
                  <span>{r === 'auto' ? 'Auto' : r}</span>
                  <span className="text-[10px] text-canvas-muted">{rl}</span>
                </button>
              ))}
            </div>
            {isImage && (
              <div className="flex w-36 flex-col gap-1">
                {RESOLUTIONS.map((res) => (
                  <button
                    key={res.value}
                    type="button"
                    data-testid="resolution-option"
                    onMouseEnter={() => onChange({ resolution: res.value })}
                    onClick={() => pick({ resolution: res.value })}
                    className={`nodrag flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-xs ${
                      (gen.resolution ?? '1k') === res.value
                        ? 'border-canvas-strong font-bold text-canvas-text'
                        : 'border-canvas-line text-canvas-text'
                    }`}
                  >
                    <span>{res.label}</span>
                    <span className="text-[10px] text-canvas-muted">
                      {res.hint}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </Pop>
      )}
      {open === 'quality' && (
        <Pop title="Quality">
          <div className="flex gap-1">
            {QUALITIES.map((q) => (
              <button
                key={q.label}
                type="button"
                data-testid="quality-option"
                onClick={() => pick({ quality: q.value })}
                className={`nodrag rounded-lg px-2.5 py-1 text-xs ${
                  (gen.quality ?? undefined) === q.value
                    ? 'bg-canvas-strong font-bold text-canvas-card'
                    : 'text-canvas-text'
                }`}
              >
                {q.label}
              </button>
            ))}
          </div>
        </Pop>
      )}
      {open === 'count' && (
        <Pop title="Count">
          <div className="grid w-40 grid-cols-4 gap-1">
            {Array.from({ length: 8 }, (_, i) => i + 1).map((n) => (
              <button
                key={n}
                type="button"
                data-testid="count-option"
                onClick={() => pick({ count: n })}
                className={`nodrag rounded-lg px-2 py-1 text-xs ${
                  (gen.count ?? 1) === n
                    ? 'bg-canvas-strong font-bold text-canvas-card'
                    : 'text-canvas-text'
                }`}
              >
                {n}
              </button>
            ))}
          </div>
        </Pop>
      )}
    </div>
  );
}

function Pill({
  children,
  onClick,
  onHover,
  disabled,
  testid,
  ariaLabel,
  className = '',
}: {
  children: React.ReactNode;
  onClick: () => void;
  /** IC hover-to-open: pointer entering the pill opens its popover. */
  onHover?: () => void;
  disabled?: boolean;
  testid: string;
  ariaLabel?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testid}
      aria-label={ariaLabel}
      onClick={onClick}
      onMouseEnter={onHover}
      disabled={disabled}
      className={`nodrag flex shrink-0 items-center gap-1 rounded-full border border-canvas-line bg-transparent px-2 py-0.5 text-xs text-canvas-text hover:border-canvas-strong/50 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    >
      {children}
    </button>
  );
}

function Pop({ title, children }: { title: string; children: React.ReactNode }) {
  // Opens DOWNWARD: anchored above (`bottom-full`) it covered the prompt box
  // and its image chips, and collided with the mention popover. Below the
  // pills there is only canvas.
  //
  // The offset to the pills is PADDING, not margin. Hover-to-open is IC
  // parity, and the row closes on mouseleave — with a margin, the pointer
  // travelling from pill to popover crosses space that belongs to neither,
  // fires mouseleave, and the menu vanishes mid-reach. Padding keeps the same
  // visual gap inside the container, so the path stays covered.
  return (
    <div className="absolute left-0 top-full z-50 pt-2">
      <div className="canvas-island rounded-xl p-2">
        <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-canvas-muted">
          {title}
        </div>
        {children}
      </div>
    </div>
  );
}

function PopRow({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`nodrag flex items-center justify-between rounded-lg px-2 py-1 text-left text-xs ${
        active ? 'font-bold text-canvas-text' : 'text-canvas-text'
      } hover:bg-canvas-card`}
    >
      <span className="truncate">{label}</span>
      {active && <span className="ml-2 h-1.5 w-1.5 rounded-full bg-canvas-strong" />}
    </button>
  );
}
