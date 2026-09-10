/**
 * "⏰ Later" — arm a one-shot wake-up carrying the message in the composer
 * (harness 2b-2 §5-2).
 *
 * The explain line is not decoration: what a wake-up does depends on what the
 * agent is doing when it fires, and a person who does not know that will read
 * a wake-up that started a whole new turn as a bug.
 */
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock } from 'lucide-react';

import { createIssueWakeup } from '../../services/schedulesService';
import { wakeupPresets } from './laterPresets';

interface LaterPopoverProps {
  issueId: number;
  /** The composer's current draft — it is what the wake-up will send. */
  text: string;
  onClose: () => void;
  onScheduled: () => void;
}

/** `datetime-local` wants local wall-clock without a zone. */
function toLocalInput(d: Date): string {
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const LaterPopover: React.FC<LaterPopoverProps> = ({ issueId, text, onClose, onScheduled }) => {
  const { t } = useTranslation();
  const presets = useMemo(() => wakeupPresets(new Date()), []);
  const [presetKey, setPresetKey] = useState<string>(presets[0]?.key ?? 'in1h');
  const [customOpen, setCustomOpen] = useState(false);
  const [custom, setCustom] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const body = text.trim();
  // A custom value wins over the preset — it is the more deliberate choice.
  const chosen = customOpen && custom ? new Date(custom) : presets.find((p) => p.key === presetKey)?.at ?? null;
  const usable = !!chosen && Number.isFinite(chosen.getTime());

  const submit = async (): Promise<void> => {
    if (!body || !usable || pending) return;
    setPending(true);
    setError(null);
    try {
      await createIssueWakeup(issueId, { fireAt: chosen as Date, text: body });
      onScheduled();
    } catch (err) {
      console.error('[LaterPopover] schedule failed', err);
      setError(err instanceof Error ? err.message : t('later.error', 'Could not schedule that wake-up.'));
    } finally {
      setPending(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-label={t('later.button', 'Later')}
      data-testid="later-popover"
      className="absolute bottom-full left-0 z-20 mb-1 w-72 rounded-lg border border-ink-800 bg-ink-900 p-2 shadow-lg"
    >
      <div className="mb-1.5 flex items-center gap-1.5 text-[12px] text-info">
        <Clock size={12} /> {t('later.button', 'Later')}
      </div>
      <div className="flex flex-wrap gap-1">
        {presets.map((p) => (
          <button
            key={p.key}
            type="button"
            data-testid={`later-preset-${p.key}`}
            aria-pressed={!customOpen && presetKey === p.key}
            onClick={() => {
              setCustomOpen(false);
              setPresetKey(p.key);
            }}
            className={`rounded-full border px-2 py-0.5 text-[12px] ${
              !customOpen && presetKey === p.key
                ? 'border-info-line bg-info-soft text-info'
                : 'border-ink-700 text-ink-400 hover:text-ink-200'
            }`}
          >
            {t(p.labelKey, p.fallback)}
          </button>
        ))}
        <button
          type="button"
          data-testid="later-custom-toggle"
          aria-pressed={customOpen}
          onClick={() => setCustomOpen((v) => !v)}
          className={`rounded-full border px-2 py-0.5 text-[12px] ${
            customOpen ? 'border-info-line bg-info-soft text-info' : 'border-ink-700 text-ink-400 hover:text-ink-200'
          }`}
        >
          {t('later.custom', 'Custom')}
        </button>
      </div>
      {customOpen && (
        <input
          type="datetime-local"
          data-testid="later-custom"
          value={custom}
          min={toLocalInput(new Date())}
          onChange={(e) => setCustom(e.target.value)}
          className="mt-1.5 w-full rounded border border-ink-700 bg-ink-950 px-1.5 py-1 text-[12px] text-ink-200"
        />
      )}
      <p className="mt-1.5 text-[11px] leading-relaxed text-ink-500" data-testid="later-explain">
        {t(
          'later.explain',
          'When it fires: if the agent is running it is picked up before its next step; if idle it starts a new turn with this text; if the issue is finished nothing is sent. Fires once.',
        )}
      </p>
      {!body && (
        <p className="mt-1 text-[11px] text-warn" data-testid="later-need-text">
          {t('later.needText', 'Type the message this wake-up should send.')}
        </p>
      )}
      {error && (
        <p className="mt-1 break-words text-[11px] text-danger" data-testid="later-error">
          {error}
        </p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          data-testid="later-confirm"
          disabled={!body || !usable || pending}
          onClick={() => void submit()}
          className="rounded border border-info-line bg-info-soft px-2 py-1 text-[12px] text-info hover:brightness-110 disabled:opacity-40"
        >
          {t('later.confirm', 'Schedule')}
        </button>
        <button
          type="button"
          data-testid="later-cancel"
          onClick={onClose}
          className="ml-auto px-2 py-1 text-[12px] text-ink-400 hover:text-ink-200"
        >
          {t('common.cancel', 'Cancel')}
        </button>
      </div>
    </div>
  );
};
