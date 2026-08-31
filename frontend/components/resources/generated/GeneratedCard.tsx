// frontend/components/resources/generated/GeneratedCard.tsx
//
// One generation in the inbox grid. The card's whole job is to say three
// things a writer needs before deciding: what it is (thumb + title), where it
// came from (a source line that can take you back), and what state it is in —
// then offer exactly the actions that state allows.

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Check, ExternalLink, Trash2 } from 'lucide-react';

import type { GeneratedItem, ReviewState } from '../../../services/generatedService';
import {
  generatedMediaCoverUrl,
  generatedMediaStreamUrl,
} from '../../../services/generatedMediaService';

export interface GeneratedCardProps {
  item: GeneratedItem;
  selected: boolean;
  /** Team scope for the (P2) asset route. Absent in a personal scope. */
  teamId?: string;
  /** Disables the mutating actions while this card's request is in flight. */
  busy?: boolean;
  onToggleSelect: (id: string) => void;
  onSave: (item: GeneratedItem) => void;
  onSaveAsAsset: (item: GeneratedItem) => void;
  onDelete: (item: GeneratedItem) => void;
}

/** State pill: `New` warns (it wants attention), `Asset` is the finished
 *  state, `Saved` is neither — a neutral chip, not a status colour. */
const STATE_PILL: Record<ReviewState, { labelKey: string; fallback: string; className: string }> = {
  unreviewed: {
    labelKey: 'generated.state.new',
    fallback: 'New',
    className: 'bg-warn-soft text-warn border-warn-line',
  },
  saved: {
    labelKey: 'generated.state.saved',
    fallback: 'Saved',
    className: 'bg-island-2 text-content-3 border-line-strong',
  },
  in_assets: {
    labelKey: 'generated.state.asset',
    fallback: 'Asset',
    className: 'bg-ok-soft text-ok border-ok-line',
  },
  // A `deleted` row only ever reaches the UI inside a cleanup preview, which
  // renders bare thumbs — but the map must be total so a future caller cannot
  // land on `undefined`.
  deleted: {
    labelKey: 'generated.state.deleted',
    fallback: 'Deleted',
    className: 'bg-danger-soft text-danger border-danger-line',
  },
};

const ACTION_BTN =
  'flex-1 rounded-md border border-line-strong bg-card px-2 py-1 text-[11px] font-medium ' +
  'text-content transition-colors hover:border-line-strong hover:bg-island-2 ' +
  'disabled:cursor-not-allowed disabled:opacity-50';

const ACTION_BTN_PRIMARY =
  'flex-1 rounded-md border border-accent bg-accent px-2 py-1 text-[11px] font-medium ' +
  'text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50';

const ACTION_BTN_GHOST =
  'flex-1 rounded-md border border-transparent px-2 py-1 text-[11px] font-medium ' +
  'text-content-3 disabled:cursor-not-allowed disabled:opacity-60';

export const GeneratedCard: React.FC<GeneratedCardProps> = ({
  item,
  selected,
  teamId,
  busy = false,
  onToggleSelect,
  onSave,
  onSaveAsAsset,
  onDelete,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Inline, not a modal: discarding one generation is a low-stakes, high-
  // frequency action, and a dialog per card would make triaging an inbox of
  // 40 unbearable. Two clicks is still two clicks.
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const pill = STATE_PILL[item.review_state];
  const isVideo = item.media_kind === 'video';
  const coverUrl = generatedMediaCoverUrl(item.id);

  const created = new Date(item.created_at);
  const createdLabel = Number.isNaN(created.getTime())
    ? item.created_at
    : created.toLocaleDateString();
  const metaLine = [item.model, createdLabel].filter(Boolean).join(' · ');

  const deepLink = item.source.deep_link;
  const sourceHint = item.source.node_id
    ? t('generated.card.openCanvasNodeHint', 'Opens the canvas at this node')
    : t('generated.card.openSourceHint', 'Opens where this was generated');

  // The P2 asset route. It is `resources/assets/item/:assetId`, NOT
  // `resources/assets/:assetId` — P2 gave the item page its own `item/`
  // segment precisely so a snowflake can never be read as a type slug. This
  // line was written before P2 shipped and pointed at the type route, where
  // an id is not one of the six slugs, so `AssetsView` bounced it to the
  // shelf: "Open asset" opened the whole library with nothing saying it had
  // failed to find the asset.
  //
  // `source_asset_id` can still be null on a row promoted before the asset
  // link existed; that lands on the index deliberately, which is why the
  // `item/` segment is part of the id branch and not of the prefix.
  const assetBase = `${teamId ? `/team/${teamId}` : ''}/resources/assets`;
  const assetPath = item.source_asset_id
    ? `${assetBase}/item/${item.source_asset_id}`
    : assetBase;

  return (
    <div
      className="group relative overflow-hidden rounded-xl border border-line bg-card"
      data-testid="generated-card"
      data-generation-id={item.id}
      data-review-state={item.review_state}
    >
      <div className="relative aspect-square bg-island-2">
        {isVideo ? (
          <video
            src={generatedMediaStreamUrl(item.id)}
            poster={coverUrl}
            muted
            preload="metadata"
            className="h-full w-full object-cover"
            aria-label={item.title}
          />
        ) : (
          <img src={coverUrl} alt={item.title} className="h-full w-full object-cover" />
        )}

        <span
          className={`absolute left-1.5 top-1.5 rounded-full border px-1.5 text-[10px] font-semibold ${pill.className}`}
        >
          {t(pill.labelKey, pill.fallback)}
        </span>

        {/* Always in the DOM (a hover-only checkbox is unreachable by
            keyboard and by test); only its chrome fades in. */}
        <label
          className={`absolute right-1.5 top-1.5 flex h-5 w-5 cursor-pointer items-center justify-center rounded border transition-opacity ${
            selected
              ? 'border-accent bg-accent text-white opacity-100'
              : 'border-line-strong bg-card text-transparent opacity-0 group-hover:opacity-100 focus-within:opacity-100'
          }`}
        >
          <input
            type="checkbox"
            className="sr-only"
            checked={selected}
            aria-label={t('generated.card.select', {
              title: item.title,
              defaultValue: 'Select {{title}}',
            })}
            onChange={() => onToggleSelect(item.id)}
          />
          <Check size={12} aria-hidden="true" />
        </label>
      </div>

      <div className="px-2 pb-1.5 pt-2 text-[11px] leading-snug text-content-3">
        <div className="truncate font-medium text-content" title={item.title}>
          {item.title}
        </div>
        {deepLink ? (
          <button
            type="button"
            title={sourceHint}
            onClick={() => navigate(deepLink)}
            className="flex w-full items-center gap-1 truncate text-left text-content-3 hover:text-content"
          >
            <ExternalLink size={11} aria-hidden="true" className="shrink-0" />
            <span className="truncate">{item.source.label}</span>
          </button>
        ) : (
          <div className="truncate" title={item.source.label}>
            {item.source.label}
          </div>
        )}
        <div className="truncate tabular-nums text-content-4" title={metaLine}>
          {metaLine}
        </div>
      </div>

      <div className="flex gap-1 px-2 pb-2">
        {confirmingDelete ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setConfirmingDelete(false);
                onDelete(item);
              }}
              className={`${ACTION_BTN} border-danger-line text-danger`}
            >
              {t('generated.card.confirmDelete', 'Confirm Delete')}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDelete(false)}
              className={ACTION_BTN}
            >
              {t('common.cancel', 'Cancel')}
            </button>
          </>
        ) : item.review_state === 'unreviewed' ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => onSave(item)}
              className={ACTION_BTN}
            >
              {t('generated.action.save', 'Save')}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onSaveAsAsset(item)}
              className={ACTION_BTN_PRIMARY}
            >
              {t('generated.action.saveAsAsset', 'As Asset…')}
            </button>
            <button
              type="button"
              disabled={busy}
              aria-label={t('generated.action.delete', 'Delete')}
              onClick={() => setConfirmingDelete(true)}
              className="shrink-0 rounded-md border border-line-strong px-1.5 py-1 text-content-4 transition-colors hover:border-danger-line hover:text-danger disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Trash2 size={12} aria-hidden="true" />
            </button>
          </>
        ) : item.review_state === 'saved' ? (
          <>
            <button type="button" disabled className={ACTION_BTN_GHOST}>
              {t('generated.action.inMyUploads', 'In My Uploads')}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onSaveAsAsset(item)}
              className={ACTION_BTN}
            >
              {t('generated.action.saveAsAsset', 'As Asset…')}
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => navigate(assetPath)}
            className={ACTION_BTN}
          >
            {t('generated.action.openAsset', 'Open asset')}
          </button>
        )}
      </div>
    </div>
  );
};
