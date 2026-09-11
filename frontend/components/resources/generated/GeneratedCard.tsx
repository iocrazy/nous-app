// frontend/components/resources/generated/GeneratedCard.tsx
//
// One generation in the inbox grid. The card's whole job is to say three
// things a writer needs before deciding: what it is (thumb + title), where it
// came from (a source line that can take you back), and what state it is in —
// then offer exactly the actions that state allows.

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ExternalLink, FolderInput, PackagePlus, Trash2 } from 'lucide-react';

import type { GeneratedItem, ReviewState } from '../../../services/generatedService';
import {
  generatedMediaCoverUrl,
  generatedMediaStreamUrl,
} from '../../../services/generatedMediaService';
import { mediaFormatLabel, placeholderFor } from '../mediaKindPlaceholder';
import { SelectionCheck } from '../SelectionCheck';

export interface GeneratedCardProps {
  item: GeneratedItem;
  selected: boolean;
  /** Team scope for the (P2) asset route. Absent in a personal scope. */
  teamId?: string;
  /** Disables the mutating actions while this card's request is in flight. */
  busy?: boolean;
  onToggleSelect: (id: string) => void;
  /** Open the full-size viewer. Absent → the thumbnail is not clickable. */
  onOpen?: (item: GeneratedItem) => void;
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

const ACTION_BTN_GHOST =
  'flex-1 rounded-md border border-transparent px-2 py-1 text-[11px] font-medium ' +
  'text-content-3 disabled:cursor-not-allowed disabled:opacity-60';

// Icon buttons, because at 150px a card cannot hold three worded buttons
// without truncating all three into nonsense. Every one carries BOTH a
// `title` (hover) and an `aria-label` (screen readers, and what the tests
// address them by) — an icon with neither is a button whose meaning exists
// only in the designer's head.
const ICON_BTN =
  'flex h-7 flex-1 items-center justify-center rounded-md border border-line-strong ' +
  'bg-card text-content-2 transition-colors hover:bg-island-2 hover:text-content ' +
  'disabled:cursor-not-allowed disabled:opacity-50';

const ICON_BTN_PRIMARY =
  'flex h-7 flex-1 items-center justify-center rounded-md border border-accent ' +
  'bg-accent text-white transition-colors hover:opacity-90 ' +
  'disabled:cursor-not-allowed disabled:opacity-50';

const ICON_BTN_DANGER =
  'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line-strong ' +
  'text-content-4 transition-colors hover:border-danger-line hover:text-danger ' +
  'disabled:cursor-not-allowed disabled:opacity-50';

export const GeneratedCard: React.FC<GeneratedCardProps> = ({
  item,
  selected,
  teamId,
  busy = false,
  onToggleSelect,
  onOpen,
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
  // `audio` and `file` rows have no frame to show. Before this they fell to
  // the `<img>` below and rendered as a broken image — an audio file saved
  // through "As Asset" (P6) looked like a failed generation.
  const placeholder = placeholderFor(item.media_kind);
  const coverUrl = generatedMediaCoverUrl(item.id);

  const created = new Date(item.created_at);
  const createdLabel = Number.isNaN(created.getTime())
    ? item.created_at
    : created.toLocaleDateString();
  const metaLine = [item.model, createdLabel].filter(Boolean).join(' · ');

  // Labels and hints, resolved once.
  //
  // `aria-label` is the SHORT name; `title` is the short name followed by the
  // hint. The two are not the same string, deliberately — a screen reader
  // announcing the whole explanation on every card is noise, while a hover
  // tooltip that only repeats the icon's name teaches nothing. Building the
  // title from `label` rather than the hint alone is what puts the new
  // "Save To Uploads" wording in front of a sighted mouse user; before this
  // it appeared only in the batch bar and the lightbox.
  const saveLabel = t('generated.action.save', 'Save To Uploads');
  const saveHint = t(
    'generated.action.saveHint',
    'Turn this into a regular file in My Uploads',
  );
  const assetLabel = t('generated.action.saveAsAsset', 'As Asset');
  const assetHint = t(
    'generated.action.saveAsAssetHint',
    "Attach it to an asset card's slot (character, location, …)",
  );
  const deleteLabel = t('generated.action.delete', 'Delete');
  // `Label — hint`. Delete has no hint, so its title is just the label.
  const hintTitle = (label: string, hint: string) => `${label} — ${hint}`;

  const deepLink = item.source.deep_link;
  // 3a §5: "谁 · issue · run #… · 第几步". `label` is the first half (the
  // backend builds it); these two are the coordinates that tell two cards
  // from two runs of the SAME issue apart — without them both read as four
  // identical words and "which run made this one?" needs the issue opened.
  //
  // Last six digits only: the full Snowflake is 18 characters of noise on a
  // 150px card, and the tail is what the rest of the UI prints for a run.
  // `step` is compared against null, not truth-tested — steps are 0-based.
  const runTail = item.source.run_id ? item.source.run_id.slice(-6) : null;
  const sourceCoords = [
    runTail ? t('generated.card.sourceRun', { run: runTail, defaultValue: 'run #{{run}}' }) : null,
    item.source.step != null
      ? t('generated.card.sourceStep', { n: item.source.step, defaultValue: 'step {{n}}' })
      : null,
  ].filter(Boolean);
  // No provenance → the bare label. A trailing separator reads as a line that
  // got cut off.
  const sourceText = sourceCoords.length
    ? `${item.source.label} · ${sourceCoords.join(' · ')}`
    : item.source.label;
  // Three destinations, three sentences. The canvas arm is keyed off
  // `node_id`, which an `agent_run` row never has — so without its own arm a
  // run link would inherit the vague fallback and tell the reader "opens where
  // this was generated" about a link that opens an ISSUE.
  const sourceHint = item.source.node_id
    ? t('generated.card.openCanvasNodeHint', 'Opens the canvas at this node')
    : item.source.kind === 'agent_run'
      ? t('generated.card.openIssueHint', 'Opens the issue whose run produced this')
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
        {/* The thumbnail is the preview affordance — the card had none, so a
            generation could only ever be seen at 150px. A button, not an
            onClick on the <img>: it has to be tabbable and it has to say
            what it does. */}
        <button
          type="button"
          data-testid="generated-card-open"
          aria-label={t('generated.card.open', {
            title: item.title,
            defaultValue: 'Preview {{title}}',
          })}
          disabled={!onOpen}
          onClick={() => onOpen?.(item)}
          className="block h-full w-full cursor-zoom-in disabled:cursor-default"
        >
          {isVideo ? (
            <video
              src={generatedMediaStreamUrl(item.id)}
              poster={coverUrl}
              muted
              preload="metadata"
              className="h-full w-full object-cover"
              aria-hidden="true"
            />
          ) : placeholder ? (
            // No player: pressing the tile opens the lightbox, which is where
            // the audio player lives. Deliberate — this placeholder sits
            // inside the tile's own <button>, and a player in there would nest
            // interactive elements (its transport controls would fight the
            // zoom gesture). The badge is the mime subtype — the wire carries
            // no filename and no duration, and the title line under the tile
            // is the row's name.
            <span
              data-testid="generated-card-placeholder"
              data-media-kind={item.media_kind}
              className="flex h-full w-full flex-col items-center justify-center gap-1.5 text-content-4"
            >
              <placeholder.Icon size={28} aria-hidden="true" />
              <span className="max-w-full truncate px-2 text-[10px] font-medium uppercase tracking-wide">
                {mediaFormatLabel(item.mime) ??
                  t(placeholder.labelKey, placeholder.fallback)}
              </span>
            </span>
          ) : (
            <img src={coverUrl} alt="" className="h-full w-full object-cover" />
          )}
        </button>

        {/* Moved to the BOTTOM-left: the selection circle now owns top-left
            (matching My Uploads), and stacking two controls in one corner is
            how the old square check ended up hard to hit. */}
        <span
          className={`pointer-events-none absolute bottom-1.5 left-1.5 rounded-full border px-1.5 text-[10px] font-semibold ${pill.className}`}
        >
          {t(pill.labelKey, pill.fallback)}
        </span>

        {/* Always in the DOM (a hover-only control is unreachable by keyboard
            and by test); only its chrome fades in. */}
        <SelectionCheck
          checked={selected}
          label={t('generated.card.select', {
            title: item.title,
            defaultValue: 'Select {{title}}',
          })}
          onToggle={() => onToggleSelect(item.id)}
        />
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
            <span className="truncate">{sourceText}</span>
          </button>
        ) : (
          <div className="truncate" title={sourceText}>
            {sourceText}
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
              title={hintTitle(saveLabel, saveHint)}
              aria-label={saveLabel}
              onClick={() => onSave(item)}
              className={ICON_BTN}
            >
              <FolderInput size={14} aria-hidden="true" />
            </button>
            <button
              type="button"
              disabled={busy}
              title={hintTitle(assetLabel, assetHint)}
              aria-label={assetLabel}
              onClick={() => onSaveAsAsset(item)}
              className={ICON_BTN_PRIMARY}
            >
              <PackagePlus size={14} aria-hidden="true" />
            </button>
            <button
              type="button"
              disabled={busy}
              title={deleteLabel}
              aria-label={deleteLabel}
              onClick={() => setConfirmingDelete(true)}
              className={ICON_BTN_DANGER}
            >
              <Trash2 size={14} aria-hidden="true" />
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
              title={hintTitle(assetLabel, assetHint)}
              aria-label={assetLabel}
              onClick={() => onSaveAsAsset(item)}
              className={ICON_BTN}
            >
              <PackagePlus size={14} aria-hidden="true" />
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
