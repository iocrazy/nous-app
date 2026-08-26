/**
 * GenParamsBlock — the "how was this image made" strip under the prompt.
 *
 * Renders `resources.gen_params` (migration 440): a normalised dict written
 * by upload_postprocess (PNG A1111/ComfyUI metadata), promote (in-app
 * generations) and the resource_gen_params backfill. Every key is optional,
 * so each row is rendered only when its value is present — an absent key
 * means "unknown", never "0" or "—".
 *
 * Collapsed by default to a one-line chip summary (model · size · steps ·
 * cfg · seed); the full key/value grid opens on click. Seed gets its own
 * copy affordance because it is the one value people actually re-use.
 */
import { useState } from 'react';
import { ChevronDown, ChevronUp, Copy, SlidersHorizontal } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { GenParams } from '../../types';
import { useOptionalToast } from '../Toast';

const TOOL_LABELS: Record<string, string> = {
  comfyui: 'ComfyUI',
  a1111: 'A1111 / SD WebUI',
  nous: 'Nous',
};

function fmtNumber(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 1000) / 1000);
}

export function GenParamsBlock({ params }: { params: GenParams | null | undefined }) {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const [open, setOpen] = useState(false);

  if (!params || typeof params !== 'object') return null;

  const size =
    typeof params.width === 'number' && typeof params.height === 'number'
      ? `${params.width}×${params.height}`
      : params.size || null;

  // Ordered rows; label keys live under resources.genParams.*
  const rows: Array<{ key: string; label: string; value: string; mono?: boolean; copy?: boolean }> = [];
  const push = (key: string, label: string, value: unknown, extra: { mono?: boolean; copy?: boolean } = {}) => {
    if (value === null || value === undefined || value === '') return;
    if (Array.isArray(value)) {
      if (value.length === 0) return;
      rows.push({ key, label, value: value.join(', '), ...extra });
      return;
    }
    rows.push({ key, label, value: typeof value === 'number' ? fmtNumber(value) : String(value), ...extra });
  };

  push('tool', t('resources.genParams.tool', 'Tool'), params.tool ? TOOL_LABELS[params.tool] ?? params.tool : null);
  push('provider', t('resources.genParams.provider', 'Provider'), params.provider);
  push('model', t('resources.genParams.model', 'Model'), params.model, { mono: true });
  push('model_hash', t('resources.genParams.modelHash', 'Model Hash'), params.model_hash, { mono: true });
  push('loras', t('resources.genParams.loras', 'LoRAs'), params.loras, { mono: true });
  push('size', t('resources.genParams.size', 'Size'), size);
  push('aspect_ratio', t('resources.genParams.aspectRatio', 'Aspect Ratio'), params.aspect_ratio);
  push('sampler', t('resources.genParams.sampler', 'Sampler'), params.sampler, { mono: true });
  push('scheduler', t('resources.genParams.scheduler', 'Scheduler'), params.scheduler, { mono: true });
  push('steps', t('resources.genParams.steps', 'Steps'), params.steps);
  push('cfg', t('resources.genParams.cfg', 'CFG'), params.cfg);
  push('denoise', t('resources.genParams.denoise', 'Denoise'), params.denoise);
  push('seed', t('resources.genParams.seed', 'Seed'), params.seed, { mono: true, copy: true });
  push('text_encoder', t('resources.genParams.textEncoder', 'Text Encoder'), params.text_encoder, { mono: true });
  push('vae', t('resources.genParams.vae', 'VAE'), params.vae, { mono: true });

  if (rows.length === 0) return null;

  const summary = [
    params.model,
    size,
    typeof params.steps === 'number' ? `${fmtNumber(params.steps)} ${t('resources.genParams.stepsShort', 'steps')}` : null,
    typeof params.cfg === 'number' ? `cfg ${fmtNumber(params.cfg)}` : null,
    typeof params.seed === 'number' ? `seed ${params.seed}` : null,
  ].filter(Boolean) as string[];

  const copySeed = async () => {
    if (typeof params.seed !== 'number') return;
    try {
      await navigator.clipboard.writeText(String(params.seed));
      toast?.addToast(t('resources.genParams.seedCopied', 'Seed copied'), 'success');
    } catch (err) {
      console.error('Failed to copy seed:', err);
    }
  };

  return (
    <div className="mt-2" data-testid="gen-params-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 text-left rounded-lg border border-ink-700 bg-ink-800/30 px-2.5 py-1.5 hover:border-[var(--accent-border)] transition-colors"
      >
        <SlidersHorizontal size={11} className="shrink-0 text-ink-500" />
        <span className="text-[10px] uppercase tracking-wide text-ink-500 shrink-0">
          {t('resources.genParams.title', 'Generation Params')}
        </span>
        {!open && (
          <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-ink-400" data-testid="gen-params-summary">
            {summary.join(' · ')}
          </span>
        )}
        <span className="ml-auto shrink-0 text-ink-500">
          {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </span>
      </button>
      {open && (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 px-2.5 py-1.5 text-[10.5px]" data-testid="gen-params-grid">
          {rows.map((row) => (
            <div key={row.key} className="contents">
              <dt className="text-ink-500 whitespace-nowrap">{row.label}</dt>
              <dd className={`min-w-0 break-all text-ink-300 flex items-center gap-1.5 ${row.mono ? 'font-mono' : ''}`}>
                <span className="min-w-0 break-all">{row.value}</span>
                {row.copy && (
                  <button
                    type="button"
                    onClick={copySeed}
                    title={t('resources.genParams.copySeed', 'Copy seed')}
                    aria-label={t('resources.genParams.copySeed', 'Copy seed')}
                    className="shrink-0 text-ink-500 hover:text-[var(--accent-text)] transition-colors"
                  >
                    <Copy size={10} />
                  </button>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
