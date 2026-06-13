import React, { useEffect, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  DRIFT_THRESHOLD_MS,
  driftExceedsThreshold,
  formatDrift,
  measureClockDrift,
} from '../services/clockDrift';

const DISMISS_KEY = 'mediahub_clock_drift_dismissed';

/**
 * Persistent warning shown when the device clock is far off server time.
 * A wrong clock silently breaks token refresh (the May-20-clock incident:
 * 22 days behind → supabase-js never refreshed → 401 storm with a
 * navigable dead UI) — the only fix is on the user's machine, so the
 * product has to say so. Mounted on both the app shell and the login page.
 */
export const ClockDriftBanner: React.FC = () => {
  const { t } = useTranslation();
  const [driftMs, setDriftMs] = useState<number | null>(null);
  const [dismissed, setDismissed] = useState(() => {
    try {
      return sessionStorage.getItem(DISMISS_KEY) === '1';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      const drift = await measureClockDrift();
      if (!cancelled) setDriftMs(drift);
    };
    check();
    // Re-check periodically: the user may fix (or break) the clock
    // while the tab stays open.
    const id = window.setInterval(check, 5 * 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  if (
    dismissed ||
    driftMs === null ||
    !driftExceedsThreshold(driftMs, DRIFT_THRESHOLD_MS)
  ) {
    return null;
  }

  const direction = driftMs < 0
    ? t('clockDrift.behind', 'behind')
    : t('clockDrift.ahead', 'ahead');

  return (
    <div className="fixed top-0 inset-x-0 z-[70] flex items-center justify-center gap-2 px-4 py-2 bg-amber-500/95 text-ink-950 text-xs font-medium shadow-lg">
      <AlertTriangle size={14} className="shrink-0" />
      <span>
        {t(
          'clockDrift.warning',
          'Your device clock is {{amount}} {{direction}} server time — this breaks sign-in. Enable automatic date & time in system settings.',
          { amount: formatDrift(driftMs), direction },
        )}
      </span>
      <button
        onClick={() => {
          setDismissed(true);
          try {
            sessionStorage.setItem(DISMISS_KEY, '1');
          } catch (err) {
            console.error('Failed to persist clock-drift dismissal:', err);
          }
        }}
        className="ml-2 p-0.5 rounded hover:bg-amber-600/40 transition-colors"
        title={t('common.dismiss', 'Dismiss')}
      >
        <X size={13} />
      </button>
    </div>
  );
};
