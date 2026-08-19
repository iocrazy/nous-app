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
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import type { PromptGenSettings } from '../types';

export interface FooterModel {
  name: string;
  display_name?: string;
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

type PopKey = 'model' | 'size' | 'quality' | 'count';

export function GenFooterControls({
  gen,
  models,
  onChange,
  disabled,
}: GenFooterControlsProps) {
  const [open, setOpen] = useState<PopKey | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Click-away closes whichever popover is open.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(null);
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [open]);

  const toggle = (key: PopKey) => setOpen((cur) => (cur === key ? null : key));
  const pick = (patch: Partial<PromptGenSettings>) => {
    onChange(patch);
    setOpen(null);
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
    <div ref={rootRef} className="relative flex min-w-0 items-center gap-1">
      <Pill
        testid="pill-model"
        ariaLabel="Generation model"
        onClick={() => toggle('model')}
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
        disabled={disabled}
      >
        <Scan size={11} />
        <span>
          {ratioValue}
          {isImage ? ` · ${(gen.resolution ?? '1k').toUpperCase()}` : ''}
        </span>
      </Pill>
      {isImage && (
        <Pill
          testid="pill-quality"
          ariaLabel="Quality"
          onClick={() => toggle('quality')}
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
                label={m.display_name || m.name}
                active={gen.model === m.name}
                onClick={() => pick({ model: m.name })}
              />
            ))}
          </div>
        </Pop>
      )}
      {open === 'size' && (
        <Pop title="Size">
          {/* IC 尺寸选择: roomy two-column panel — ratios left, resolution
              ladder right; HOVER selects (滑动到即选择), click closes. */}
          <div className="flex w-[380px] gap-3">
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              {FOOTER_RATIOS.map((r) => (
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
                  <span>{r}</span>
                  <span className="text-[10px] text-canvas-muted">
                    {RATIO_LABELS[r]}
                  </span>
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
  disabled,
  testid,
  ariaLabel,
  className = '',
}: {
  children: React.ReactNode;
  onClick: () => void;
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
      disabled={disabled}
      className={`nodrag flex shrink-0 items-center gap-1 rounded-full border border-canvas-line bg-transparent px-2 py-0.5 text-xs text-canvas-text hover:border-canvas-strong/50 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    >
      {children}
    </button>
  );
}

function Pop({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="canvas-island absolute bottom-full left-0 z-50 mb-2 rounded-xl p-2">
      <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-canvas-muted">
        {title}
      </div>
      {children}
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
