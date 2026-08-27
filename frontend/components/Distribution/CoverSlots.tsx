// components/Distribution/CoverSlots.tsx
//
// The publish page's cover section, reduced to what it is: two slots. Each
// is a button that opens Cover Studio on the matching tab — the studio is
// where a cover comes from now (frame crop, upload, or the model), so the
// old "sample frames here" card and the separate "AI covers" card were two
// extra ways of saying "open the studio". A filled slot shows the cover and
// carries a small clear control; publishing only needs one of the two.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Film, X } from 'lucide-react';
import { getSupabaseClient } from '../../supabaseClient';
import { getResourceFileUrl } from '../../services/resourceService';

export type CoverOrientation = 'vertical' | 'horizontal';

export interface CoverPair {
  /**
   * Both optional: the studio fills one slot per apply (the vertical tab a
   * 3:4 cover, the horizontal tab a 4:3 one). Publishing only ever uses one
   * of the two (publish_distribution.py: vertical first, horizontal as
   * fallback), and the gate requires "either", not "both".
   */
  vertical?: string;
  horizontal?: string;
}

export interface CoverSlotsProps {
  value: CoverPair | null;
  /** Open Cover Studio on this tab. */
  onOpen: (orientation: CoverOrientation) => void;
  /** Empty one slot. */
  onClear: (orientation: CoverOrientation) => void;
  /**
   * Why the slots cannot be used right now (no video selected). The tiles
   * stay visible but dimmed, and a click hands the reason to `onBlocked`
   * instead of opening anything — a control that looks usable and silently
   * does nothing is the failure this page keeps re-learning.
   */
  blockedReason?: string | null;
  onBlocked?: (reason: string) => void;
}

export const CoverSlots: React.FC<CoverSlotsProps> = ({ value, onOpen, onClear, blockedReason = null, onBlocked }) => {
  const { t } = useTranslation();
  // Signed URL transport for <img src> — headers are impossible there, and
  // the file endpoint accepts the Supabase JWT as ?token=.
  const [token, setToken] = useState<string | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    const supabase = getSupabaseClient();
    if (!supabase) return undefined;
    void supabase.auth.getSession()
      .then(({ data }) => { if (alive) setToken(data?.session?.access_token); })
      .catch((err) => console.error('distribution: read session for cover urls failed', err));
    return () => { alive = false; };
  }, []);

  const slot = (orientation: CoverOrientation) => {
    const kind = orientation === 'vertical' ? 'v' : 'h';
    const resourceId = value?.[orientation];
    const label = orientation === 'vertical'
      ? t('distribution.publish.vertical34', 'Vertical 3:4')
      : t('distribution.publish.horizontal43', 'Horizontal 4:3');
    return (
      <div className="cover-cell">
        <button
          type="button"
          className={`cover-slot ${kind} pick ${resourceId ? 'filled' : ''} ${blockedReason ? 'blocked' : ''}`}
          aria-disabled={blockedReason ? true : undefined}
          title={blockedReason ?? undefined}
          onClick={() => (blockedReason ? onBlocked?.(blockedReason) : onOpen(orientation))}
          aria-label={resourceId
            ? t('distribution.publish.changeCoverAria', { defaultValue: 'Change {{label}}', label })
            : t('distribution.publish.chooseCoverAria', { defaultValue: 'Choose {{label}}', label })}
          data-testid={orientation === 'vertical' ? 'open-cover-studio' : 'open-cover-studio-h'}
        >
          {resourceId ? <img src={getResourceFileUrl(resourceId, token)} alt={label} /> : <Film />}
          {!resourceId && <span>{t('distribution.publish.chooseCover', 'Choose cover')}</span>}
        </button>
        {resourceId && (
          <button
            type="button"
            className="cover-clear"
            onClick={() => onClear(orientation)}
            aria-label={t('distribution.publish.clearCoverAria', { defaultValue: 'Remove {{label}}', label })}
            data-testid={`cover-clear-${kind}`}
          >
            <X size={11} />
          </button>
        )}
        <span className="cover-cap">{label}</span>
      </div>
    );
  };

  return (
    <div className="cover-slots" data-testid="cover-slots">
      {slot('vertical')}
      {slot('horizontal')}
    </div>
  );
};
