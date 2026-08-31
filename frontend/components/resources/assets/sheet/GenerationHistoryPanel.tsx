// frontend/components/resources/assets/sheet/GenerationHistoryPanel.tsx
//
// "What has this asset produced" - the right column's last panel.
//
// It exists now because `GET /generated` finally takes `source_asset_id`
// (Task 8's backend half). Task 7 left the panel out on purpose: without the
// filter the only thing it could have shown was the scope's WHOLE inbox
// rendered under this asset's name, which is a false claim rather than a
// partial feature. The filtered call is the whole difference.
//
// `state: 'all'` deliberately. The default is `unreviewed`, and a history that
// dropped a generation the moment it was saved or attached would answer "what
// did this asset produce" with "what has nobody looked at yet".
//
// PRESETS FETCH TOO. A system preset can never be a source (`generate_slot`
// goes through `_require_writable`, which refuses one), so the answer is
// always empty there - but it is fetched rather than assumed, because an
// empty state derived from a rule is a lie waiting for the rule to change.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

import { fetchGenerated } from '../../../../services/generatedService';
import type { GeneratedItem } from '../../../../services/generatedService';
import { generatedMediaCoverUrl } from '../../../../services/generatedMediaService';

/** Enough to show the shape of a history without turning the sidebar into a
 *  second inbox — the inbox itself is one click away. */
const HISTORY_LIMIT = 8;

export interface GenerationHistoryPanelProps {
  scopeId: string;
  assetId: string;
  /** Navigate to the Generated inbox (the page owns `resPath`). */
  onOpenInbox: () => void;
}

export const GenerationHistoryPanel: React.FC<GenerationHistoryPanelProps> = ({
  scopeId,
  assetId,
  onOpenInbox,
}) => {
  const { t } = useTranslation();
  const [items, setItems] = useState<GeneratedItem[]>([]);
  const [loading, setLoading] = useState(true);
  /** A failed fetch is NOT an empty history — "this asset has generated
   *  nothing" is a claim, and we only get to make it when we know. */
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchGenerated(scopeId, {
      sourceAssetId: assetId,
      state: 'all',
      limit: HISTORY_LIMIT,
    })
      .then((page) => {
        if (!alive) return;
        setItems(page.items);
        setFailed(false);
      })
      .catch((err: unknown) => {
        console.error('[GenerationHistoryPanel] history unavailable:', err);
        if (!alive) return;
        setItems([]);
        setFailed(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [scopeId, assetId]);

  return (
    <section data-testid="generation-history" className="flex flex-col gap-1.5">
      <h3 className="text-[11px] font-medium uppercase tracking-wide text-content-4">
        {t('assets.history.title', 'Generated From This')}
      </h3>

      {loading ? (
        <p className="flex items-center gap-1.5 text-[11px] text-content-4">
          <Loader2 size={11} className="animate-spin" aria-hidden="true" />
          {t('common.loading', 'Loading...')}
        </p>
      ) : failed ? (
        <p role="alert" data-testid="history-failed" className="text-[11px] text-danger">
          {t('assets.history.unavailable', 'Generation History Unavailable')}
        </p>
      ) : items.length === 0 ? (
        <p className="text-[11px] text-content-4">
          {t('assets.history.empty', 'Nothing Generated Yet')}
        </p>
      ) : (
        <>
          <ul className="grid grid-cols-4 gap-1">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  data-testid="history-item"
                  data-generation-id={item.id}
                  data-review-state={item.review_state}
                  onClick={onOpenInbox}
                  title={item.title}
                  className="block aspect-square w-full overflow-hidden rounded border border-line bg-island-2 hover:border-line-strong"
                >
                  <img
                    src={generatedMediaCoverUrl(item.id)}
                    alt={item.title}
                    className="h-full w-full object-cover"
                  />
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            data-testid="history-open-inbox"
            onClick={onOpenInbox}
            className="self-start text-[11px] font-medium text-accent hover:underline"
          >
            {t('assets.history.openInbox', 'Review In Generated')}
          </button>
        </>
      )}
    </section>
  );
};
