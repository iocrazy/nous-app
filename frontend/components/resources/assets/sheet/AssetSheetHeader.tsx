// frontend/components/resources/assets/sheet/AssetSheetHeader.tsx
//
// The top of every entity sheet: portrait, name, readiness, role line, an
// inline-editable description, and the per-type extras.
//
// "Six sheets differ only in header extras" (spec 6.2) is taken literally -
// there is ONE header, and the type-specific facts are two small chips inside
// it (a location's interior/exterior, an audio asset's loopable + duration)
// rather than six headers that would drift apart.
//
// Inline edit rules:
//
//  * Each field PATCHes ONLY ITSELF. `AssetUpdate` is `exclude_unset`, so a
//    body built from the whole header would rewrite fields the user never
//    opened.
//  * An unchanged value sends NOTHING. Blurring out of a field you only looked
//    at must not bump `updated_at` and must not appear in the audit trail as an
//    edit.
//  * A READ-ONLY preset renders text, never inputs. The server answers 403
//    `system_preset_readonly`, and an input whose every save is refused is a
//    worse answer than no input.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BookmarkMinus, BookmarkPlus, Check, Lock, Pencil, Repeat, X } from 'lucide-react';

import type { AssetRowDetail, AssetUpdateBody } from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import { ASSET_TYPE_ICON, slotLabelKey, typeSingularKey } from '../assetTypeMeta';
import { audioDurationSec, audioLoopable, formatDuration } from './assetSheetModel';

export interface AssetSheetHeaderProps {
  detail: AssetRowDetail;
  readOnly: boolean;
  /** Patch one field. The caller re-fetches on success and reports failures. */
  onPatch: (body: AssetUpdateBody) => void;
  /**
   * Flip library membership (mig 448). A NAMED action with its own routes, not
   * a field write — which is why it is a separate prop rather than another
   * `onPatch` call site. Omitted where no caller can perform it; the control
   * then renders as a plain state chip, because a button whose every click is
   * a no-op is worse than no button.
   */
  onToggleLibrary?: () => void;
  /** Loadout chips (characters only) render under the role line. */
  children?: React.ReactNode;
}

export const AssetSheetHeader: React.FC<AssetSheetHeaderProps> = ({
  detail,
  readOnly,
  onPatch,
  onToggleLibrary,
  children,
}) => {
  const { t } = useTranslation();
  const Icon = ASSET_TYPE_ICON[detail.asset_type];
  const ready = detail.readiness.state === 'ready';
  const missing = detail.readiness.missing
    .map((slot) => t(slotLabelKey(slot), slot))
    .join(', ');

  return (
    <header data-testid="asset-sheet-header" className="flex gap-4">
      <div className="h-28 w-24 shrink-0 overflow-hidden rounded-xl border border-line bg-island-2">
        {detail.cover_file_id ? (
          <img
            src={getResourceCoverUrl(detail.cover_file_id)}
            alt={detail.name}
            data-testid="sheet-portrait"
            className="h-full w-full object-cover"
          />
        ) : (
          <span className="flex h-full w-full items-center justify-center text-content-4">
            <Icon size={26} />
          </span>
        )}
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <InlineText
          testId="sheet-name"
          value={detail.name}
          label={t('assets.dialog.name', 'Name')}
          readOnly={readOnly}
          maxLength={200}
          className="text-lg font-semibold text-content"
          onSave={(next) => onPatch({ name: next })}
          allowEmpty={false}
        />

        <div className="flex flex-wrap items-center gap-1.5">
          <span
            data-testid="sheet-readiness"
            data-readiness={detail.readiness.state}
            className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${
              ready
                ? 'border-ok-line bg-ok-soft text-ok'
                : 'border-warn-line bg-warn-soft text-warn'
            }`}
          >
            {ready
              ? t('assets.readiness.ready', 'Ready')
              : missing === ''
                ? t('assets.readiness.draft', 'Draft')
                : `${t('assets.readiness.draft', 'Draft')} - ${t('assets.card.missing', {
                    slots: missing,
                    defaultValue: 'Missing: {{slots}}',
                  })}`}
          </span>

          <span className="rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-content-3">
            {t(typeSingularKey(detail.asset_type), detail.asset_type)}
          </span>

          {/* Library membership. ALWAYS shown here, unlike on the card where
              only the "out" state gets a badge: the sheet is where the user
              acts on it, so the chip has to say which state they are acting
              FROM. Read-only rows (system presets) get the chip without the
              button — the server answers 403 for a preset, and offering a
              click whose only outcome is a refusal is what this file already
              refuses to do for the inline fields. */}
          {readOnly || !onToggleLibrary ? (
            <span
              data-testid="sheet-library"
              data-in-library={detail.in_library}
              className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                detail.in_library
                  ? 'border-line-strong text-content-3'
                  : 'border-warn-line bg-warn-soft text-warn'
              }`}
            >
              {detail.in_library
                ? t('assets.library.in', 'In Library')
                : t('assets.library.notInLibrary', 'Not In Library')}
            </span>
          ) : (
            <button
              type="button"
              data-testid="sheet-library-toggle"
              data-in-library={detail.in_library}
              onClick={onToggleLibrary}
              title={
                detail.in_library
                  ? t(
                      'assets.library.removeHint',
                      'Takes it off your library shelf — it stays in this project',
                    )
                  : t(
                      'assets.library.addHint',
                      'Puts it on your library shelf so other projects can use it',
                    )
              }
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors ${
                detail.in_library
                  ? 'border-line-strong text-content-3 hover:text-content'
                  : 'border-warn-line bg-warn-soft text-warn hover:opacity-80'
              }`}
            >
              {detail.in_library ? (
                <BookmarkMinus size={10} aria-hidden="true" />
              ) : (
                <BookmarkPlus size={10} aria-hidden="true" />
              )}
              {detail.in_library
                ? t('assets.library.in', 'In Library')
                : t('assets.library.addToLibrary', 'Add To Library')}
            </button>
          )}

          {detail.is_system_preset && (
            <span
              data-testid="preset-badge"
              className="inline-flex items-center gap-1 rounded-full border border-line-strong bg-island-2 px-2 py-0.5 text-[11px] text-content-3"
            >
              <Lock size={10} aria-hidden="true" />
              {t('assets.presets.title', 'System Presets')}
            </span>
          )}

          {detail.asset_type === 'audio' && (
            <>
              <span
                data-testid="audio-loopable"
                className="inline-flex items-center gap-1 rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-content-3"
              >
                <Repeat size={10} aria-hidden="true" />
                {audioLoopable(detail.attrs)
                  ? t('assets.sheet.loopable', 'Loopable')
                  : t('assets.sheet.notLoopable', 'Not Loopable')}
              </span>
              {audioDurationSec(detail.attrs) !== null && (
                <span
                  data-testid="audio-duration"
                  className="rounded-full border border-line-strong px-2 py-0.5 text-[11px] tabular-nums text-content-3"
                >
                  {formatDuration(audioDurationSec(detail.attrs) as number)}
                </span>
              )}
            </>
          )}
        </div>

        {/* The role line. ONE field for all six types - `role_tag` is writable
            only here and in the New dialog, so skipping it for locations (as an
            earlier version did, showing a static chip instead) left a location
            created with the wrong setting, or none, uncorrectable from its own
            sheet. A location renders the SAME field as its interior/exterior
            chip; only the display form differs. */}
        <InlineText
          testId="sheet-role"
          value={detail.role_tag}
          label={
            detail.asset_type === 'location'
              ? t('assets.sheet.setting', 'Setting')
              : t('assets.dialog.role', 'Role')
          }
          readOnly={readOnly}
          maxLength={40}
          className="text-[12px] text-content-3"
          placeholder={
            detail.asset_type === 'location'
              ? t('assets.sheet.addSetting', 'Add A Setting')
              : t('assets.sheet.addRole', 'Add A Role')
          }
          display={
            detail.asset_type === 'location'
              ? (value) => (
                  <span
                    data-testid="location-setting"
                    className="rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-content-3"
                  >
                    {t(`assets.setting.${value}`, value)}
                  </span>
                )
              : undefined
          }
          onSave={(next) => onPatch({ role_tag: next })}
          allowEmpty
        />

        <InlineText
          testId="sheet-description"
          value={detail.description}
          label={t('assets.dialog.description', 'Description')}
          readOnly={readOnly}
          maxLength={20000}
          multiline
          className="text-[13px] leading-relaxed text-content-2"
          placeholder={t('assets.sheet.addDescription', 'Add A Description')}
          onSave={(next) => onPatch({ description: next })}
          allowEmpty
        />

        {readOnly && (
          <p data-testid="preset-readonly-hint" className="text-[11px] text-content-4">
            {t('assets.presets.hint', 'Read-only - duplicate one to edit it')}
          </p>
        )}

        {children}
      </div>
    </header>
  );
};

// --- Inline field -----------------------------------------------------------

interface InlineTextProps {
  testId: string;
  value: string;
  label: string;
  readOnly: boolean;
  maxLength: number;
  className: string;
  placeholder?: string;
  multiline?: boolean;
  /** Whether clearing the field is a legal edit. A name is not. */
  allowEmpty: boolean;
  /** Optional read-mode presentation (a location's setting chip). The EDIT
   *  mode is unchanged: a display form is a skin, never a reason to drop the
   *  field. An empty value always falls back to the placeholder, so the
   *  affordance is on screen even when there is nothing to show. */
  display?: (value: string) => React.ReactNode;
  onSave: (next: string) => void;
}

const InlineText: React.FC<InlineTextProps> = ({
  testId,
  value,
  label,
  readOnly,
  maxLength,
  className,
  placeholder,
  multiline = false,
  allowEmpty,
  display,
  onSave,
}) => {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  // A refetch (or an agent write) that changed this field must show its new
  // value once the user is no longer editing it.
  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  if (readOnly || !editing) {
    return (
      <div className="group flex items-start gap-1.5">
        <span data-testid={testId} className={`min-w-0 ${className} whitespace-pre-wrap`}>
          {value === '' ? (
            <span className="text-content-4">{placeholder ?? ''}</span>
          ) : display ? (
            display(value)
          ) : (
            value
          )}
        </span>
        {!readOnly && (
          <button
            type="button"
            data-testid={`${testId}-edit`}
            aria-label={t('assets.sheet.editField', { field: label, defaultValue: 'Edit {{field}}' })}
            onClick={() => {
              setDraft(value);
              setEditing(true);
            }}
            className="mt-0.5 shrink-0 text-content-4 opacity-0 transition-opacity hover:text-content-2 focus:opacity-100 group-hover:opacity-100"
          >
            <Pencil size={11} aria-hidden="true" />
          </button>
        )}
      </div>
    );
  }

  const commit = () => {
    const next = draft.trim();
    if (!allowEmpty && next === '') {
      // Not an error to report: it is a cancelled edit. Sending it would be a
      // 422 for something the user did not ask for.
      setEditing(false);
      setDraft(value);
      return;
    }
    setEditing(false);
    // Unchanged means no request: blurring a field you only looked at must not
    // bump `updated_at`.
    if (next === value) return;
    onSave(next);
  };

  const shared = {
    autoFocus: true,
    value: draft,
    maxLength,
    'aria-label': label,
    'data-testid': `${testId}-input`,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setDraft(e.target.value),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        setEditing(false);
        setDraft(value);
      }
      if (e.key === 'Enter' && !multiline) commit();
    },
    className:
      'w-full rounded-lg border border-accent bg-card px-2 py-1 text-[13px] text-content outline-none',
  };

  return (
    <div className="flex items-start gap-1.5">
      {multiline ? <textarea rows={3} {...shared} /> : <input {...shared} />}
      <button
        type="button"
        data-testid={`${testId}-save`}
        aria-label={t('common.save', 'Save')}
        onClick={commit}
        className="mt-1 text-content-3 hover:text-ok"
      >
        <Check size={12} aria-hidden="true" />
      </button>
      <button
        type="button"
        aria-label={t('common.cancel', 'Cancel')}
        onClick={() => {
          setEditing(false);
          setDraft(value);
        }}
        className="mt-1 text-content-4 hover:text-content-2"
      >
        <X size={12} aria-hidden="true" />
      </button>
    </div>
  );
};
