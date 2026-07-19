/**
 * BeatsDurationControl — the Arrangement topbar "target total length" control
 * (M3). Shows the current target as a film-apostrophe chip (`45'`) or "Set
 * length" when unset; clicking opens a small popover with the preset ladder
 * (60s / 3' / 20' / 45' / 90') plus a custom seconds / `m:ss` input.
 *
 * It only REPORTS the chosen length via `onCommit`; the parent
 * (ArrangementView) owns the conform decision (resize-ruler vs stretch-beats)
 * because it holds the beats. Ink-chrome styling to match the zoom tools.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DURATION_PRESETS, formatTargetLength, parseDurationInput } from './beatsDuration';

interface Props {
  targetSec: number | null;
  onCommit: (sec: number) => void;
}

export function BeatsDurationControl({ targetSec, onCommit }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState('');
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDocDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const commit = useCallback(
    (sec: number) => {
      onCommit(sec);
      setOpen(false);
      setCustom('');
    },
    [onCommit],
  );

  const applyCustom = useCallback(() => {
    const parsed = parseDurationInput(custom);
    if (parsed != null) commit(parsed);
  }, [custom, commit]);

  return (
    <div className="mh-arr-len" ref={rootRef}>
      <button
        type="button"
        className={`mh-arr-tool-btn mh-arr-len-btn${targetSec == null ? ' unset' : ''}`}
        data-testid="arr-length-btn"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="mh-arr-len-icon" aria-hidden="true">
          ⌛
        </span>
        {targetSec == null ? t('editor.arrSetLength') : formatTargetLength(targetSec)}
      </button>

      {open && (
        <div className="mh-arr-len-pop" role="dialog" data-testid="arr-length-pop">
          <div className="mh-arr-len-presets">
            {DURATION_PRESETS.map((sec) => (
              <button
                key={sec}
                type="button"
                className={`mh-arr-len-preset${targetSec === sec ? ' selected' : ''}`}
                data-testid="arr-length-preset"
                data-sec={sec}
                onClick={() => commit(sec)}
              >
                {formatTargetLength(sec)}
              </button>
            ))}
          </div>
          <div className="mh-arr-len-custom">
            <input
              className="mh-arr-input narrow"
              data-testid="arr-length-custom"
              value={custom}
              placeholder={t('editor.arrLengthCustomPlaceholder')}
              aria-label={t('editor.arrLengthCustom')}
              onChange={(e) => setCustom(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  applyCustom();
                }
              }}
            />
            <button
              type="button"
              className="mh-arr-modal-btn primary"
              data-testid="arr-length-custom-apply"
              disabled={parseDurationInput(custom) == null}
              onClick={applyCustom}
            >
              {t('editor.arrLengthSet')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
