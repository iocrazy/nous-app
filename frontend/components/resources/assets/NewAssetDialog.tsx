// frontend/components/resources/assets/NewAssetDialog.tsx
//
// "+ New ▾" → this. Three fields, one request, and a name collision that is
// an OFFER rather than a dead end.
//
// Design notes worth keeping:
//
//  * The submit button is NOT disabled on an empty name. A disabled button
//    with no explanation leaves the user guessing which field is wrong;
//    submitting and being told "a name is required" says it. (It also keeps
//    the validation reachable — a rule enforced only by a disabled control is
//    a rule nothing can test and nothing else enforces.)
//  * `asset_exists` (409) carries `existing_asset_id`. The backend went to the
//    trouble of telling us WHICH asset collided, so the failure renders as
//    "open the one that exists" — dropping that would leave the user retyping
//    a name they cannot have. This is spec decision 14's "已存在，是否关联".
//  * The two text limits mirror `AssetCreate` (name 200, role_tag 40). Being
//    stopped by the input is a better answer than a 422 that reads as a bug.

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

import { UiButton, UiModal } from '../../ui/primitives';
import { createAsset, GeneratedApiError } from '../../../services/assetsService';
import type { AssetSummary, AssetType } from '../../../services/assetsService';
import { typeSingularKey } from './assetTypeMeta';

/** Mirrors `AssetCreate`'s `Field(max_length=...)`. */
const NAME_MAX = 200;
const ROLE_MAX = 40;

export interface NewAssetDialogProps {
  open: boolean;
  scopeId: string;
  assetType: AssetType;
  onClose: () => void;
  /** The created asset — the caller navigates to its sheet and refreshes the
   *  sidebar counts. */
  onCreated: (asset: AssetSummary) => void;
  /** The user chose to open the colliding asset instead of renaming. */
  onOpenExisting: (assetId: string) => void;
}

const FIELD =
  'w-full rounded-lg border border-line-strong bg-card px-2.5 py-1.5 text-[13px] text-content ' +
  'placeholder:text-content-4 focus:border-accent focus:outline-none';

const LABEL = 'block text-[11px] font-medium uppercase tracking-wide text-content-3';

export const NewAssetDialog: React.FC<NewAssetDialogProps> = ({
  open,
  scopeId,
  assetType,
  onClose,
  onCreated,
  onOpenExisting,
}) => {
  const { t } = useTranslation();

  const [name, setName] = useState('');
  const [roleTag, setRoleTag] = useState('');
  const [description, setDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Set only by a 409 — the id of the asset already holding this name. */
  const [existingId, setExistingId] = useState<string | null>(null);

  // Re-opening (or switching type from the six-way menu) starts clean.
  // Without this the previous attempt's error would greet the next one.
  useEffect(() => {
    if (!open) return;
    setName('');
    setRoleTag('');
    setDescription('');
    setError(null);
    setExistingId(null);
    setSubmitting(false);
  }, [open, assetType]);

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (submitting) return;

      const trimmed = name.trim();
      if (trimmed === '') {
        setExistingId(null);
        setError(t('assets.dialog.nameRequired', 'A Name Is Required'));
        return;
      }

      setSubmitting(true);
      setError(null);
      setExistingId(null);
      try {
        const created = await createAsset(scopeId, {
          asset_type: assetType,
          name: trimmed,
          // Sent even when blank: `""` is the column default, so this is the
          // same asset either way — and omitting them conditionally would
          // make the request shape depend on the form in a way nothing tests.
          role_tag: roleTag.trim(),
          description: description.trim(),
        });
        onCreated(created);
      } catch (err) {
        console.error('[NewAssetDialog] create failed:', err);
        if (err instanceof GeneratedApiError) {
          const collided =
            err.code === 'asset_exists' && typeof err.extra.existing_asset_id === 'string'
              ? err.extra.existing_asset_id
              : null;
          setExistingId(collided);
          setError(t(`assets.err.${err.code}`, t('assets.err.generic', 'Something went wrong.')));
        } else {
          setError(t('assets.err.generic', 'Something went wrong.'));
        }
      } finally {
        setSubmitting(false);
      }
    },
    [submitting, name, roleTag, description, scopeId, assetType, onCreated, t],
  );

  const typeName = t(typeSingularKey(assetType), assetType);

  return (
    <UiModal
      isOpen={open}
      onClose={onClose}
      title={t('assets.dialog.title', { type: typeName, defaultValue: 'New {{type}}' })}
    >
      <form onSubmit={submit} data-testid="new-asset-form" className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label className={LABEL} htmlFor="new-asset-name">
            {t('assets.dialog.name', 'Name')}
          </label>
          <input
            id="new-asset-name"
            className={FIELD}
            value={name}
            maxLength={NAME_MAX}
            autoFocus
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className={LABEL} htmlFor="new-asset-role">
            {t('assets.dialog.role', 'Role')}
          </label>
          <input
            id="new-asset-role"
            className={FIELD}
            value={roleTag}
            maxLength={ROLE_MAX}
            onChange={(e) => setRoleTag(e.target.value)}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className={LABEL} htmlFor="new-asset-description">
            {t('assets.dialog.description', 'Description')}
          </label>
          <textarea
            id="new-asset-description"
            className={`${FIELD} min-h-[72px] resize-y`}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        {error !== null && (
          <div
            role="alert"
            data-testid="new-asset-error"
            className="flex items-center justify-between gap-2 rounded-lg border border-danger-line bg-danger-soft px-2.5 py-1.5 text-[12px] text-danger"
          >
            <span>{error}</span>
            {existingId !== null && (
              <button
                type="button"
                onClick={() => onOpenExisting(existingId)}
                className="shrink-0 rounded-md border border-danger-line px-2 py-0.5 text-[11px] font-medium"
              >
                {t('assets.dialog.openExisting', 'Open Existing')}
              </button>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <UiButton type="button" variant="ghost" size="sm" onClick={onClose}>
            {t('common.cancel', 'Cancel')}
          </UiButton>
          <UiButton type="submit" variant="primary" size="sm" disabled={submitting}>
            {submitting && <Loader2 size={12} className="mr-1 animate-spin" aria-hidden="true" />}
            {t('assets.dialog.create', 'Create')}
          </UiButton>
        </div>
      </form>
    </UiModal>
  );
};
