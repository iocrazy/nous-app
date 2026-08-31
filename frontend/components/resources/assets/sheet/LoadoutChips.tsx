// frontend/components/resources/assets/sheet/LoadoutChips.tsx
//
// A character's outfits, as a chip row (spec 6.2). Selecting one re-filters
// the board's loadout-scoped pins; the rest of the row is the loadout CRUD the
// API exposes - create, rename, set default, delete.
//
// The one rule that has to match the server: THE DEFAULT LOADOUT CANNOT BE
// DELETED (the API answers 422). The delete control is therefore absent on the
// default rather than present-and-failing: an affordance that exists only to
// be refused teaches the user nothing except that the page is unreliable.
// Promoting another loadout first is the path, and "Set Default" is right
// there.

import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Loader2, Pencil, Plus, Star, Trash2, X } from 'lucide-react';

import {
  createLoadout,
  deleteLoadout,
  updateLoadout,
} from '../../../../services/assetsService';
import type { AssetLoadoutRow } from '../../../../services/assetsService';

export interface LoadoutChipsProps {
  scopeId: string;
  assetId: string;
  loadouts: AssetLoadoutRow[];
  selectedId: string | null;
  readOnly: boolean;
  onSelect: (loadoutId: string) => void;
  /** Any successful write - the sheet re-fetches the detail row. */
  onChanged: () => void;
  onError: (err: unknown) => void;
}

export const LoadoutChips: React.FC<LoadoutChipsProps> = ({
  scopeId,
  assetId,
  loadouts,
  selectedId,
  readOnly,
  onSelect,
  onChanged,
  onError,
}) => {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  /** The loadout whose name is being edited, or `'new'` for the create row. */
  const [editing, setEditing] = useState<string | null>(null);
  const [draftName, setDraftName] = useState('');

  const run = useCallback(
    async (work: () => Promise<unknown>) => {
      if (busy) return;
      setBusy(true);
      try {
        await work();
        setEditing(null);
        setDraftName('');
        onChanged();
      } catch (err) {
        onError(err);
      } finally {
        setBusy(false);
      }
    },
    [busy, onChanged, onError],
  );

  const submitName = useCallback(() => {
    const name = draftName.trim();
    // An empty name is not a rename - it is a cancelled one. Sending it would
    // be a 422 for something the user did not ask for.
    if (name === '') {
      setEditing(null);
      return;
    }
    if (editing === 'new') void run(() => createLoadout(scopeId, assetId, { name }));
    else if (editing) void run(() => updateLoadout(scopeId, assetId, editing, { name }));
  }, [draftName, editing, run, scopeId, assetId]);

  if (loadouts.length === 0 && readOnly) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="loadout-chips">
      <span className="text-[11px] font-medium uppercase tracking-wide text-content-4">
        {t('assets.sheet.loadouts', 'Loadouts')}
      </span>

      {loadouts.map((loadout) => {
        const active = loadout.id === selectedId;
        if (editing === loadout.id) {
          return (
            <NameField
              key={loadout.id}
              value={draftName}
              onChange={setDraftName}
              onSubmit={submitName}
              onCancel={() => setEditing(null)}
              label={t('assets.sheet.loadoutName', 'Loadout Name')}
            />
          );
        }
        return (
          <span
            key={loadout.id}
            data-testid="loadout-chip"
            data-loadout-id={loadout.id}
            data-active={active}
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors ${
              active
                ? 'border-accent bg-[var(--accent-soft)] text-accent'
                : 'border-line-strong bg-card text-content-2'
            }`}
          >
            <button
              type="button"
              aria-pressed={active}
              onClick={() => onSelect(loadout.id)}
              className="font-medium"
            >
              {loadout.name}
            </button>
            {loadout.is_default && (
              <Star
                size={10}
                aria-label={t('assets.sheet.defaultLoadout', 'Default')}
                className="text-content-4"
              />
            )}
            {!readOnly && (
              <>
                <button
                  type="button"
                  data-testid="loadout-rename"
                  aria-label={t('assets.sheet.renameLoadout', 'Rename Loadout')}
                  onClick={() => {
                    setEditing(loadout.id);
                    setDraftName(loadout.name);
                  }}
                  className="text-content-4 hover:text-content-2"
                >
                  <Pencil size={10} aria-hidden="true" />
                </button>
                {!loadout.is_default && (
                  <>
                    <button
                      type="button"
                      data-testid="loadout-set-default"
                      aria-label={t('assets.sheet.setDefaultLoadout', 'Set As Default')}
                      onClick={() =>
                        void run(() =>
                          updateLoadout(scopeId, assetId, loadout.id, { is_default: true }),
                        )
                      }
                      className="text-content-4 hover:text-content-2"
                    >
                      <Star size={10} aria-hidden="true" />
                    </button>
                    {/* Absent on the default: the API refuses that with 422,
                        and an affordance whose only outcome is a refusal is
                        worse than no affordance. */}
                    <button
                      type="button"
                      data-testid="loadout-delete"
                      aria-label={t('assets.sheet.deleteLoadout', 'Delete Loadout')}
                      onClick={() =>
                        void run(() => deleteLoadout(scopeId, assetId, loadout.id))
                      }
                      className="text-content-4 hover:text-danger"
                    >
                      <Trash2 size={10} aria-hidden="true" />
                    </button>
                  </>
                )}
              </>
            )}
          </span>
        );
      })}

      {editing === 'new' ? (
        <NameField
          value={draftName}
          onChange={setDraftName}
          onSubmit={submitName}
          onCancel={() => setEditing(null)}
          label={t('assets.sheet.loadoutName', 'Loadout Name')}
        />
      ) : (
        !readOnly && (
          <button
            type="button"
            data-testid="loadout-new"
            onClick={() => {
              setEditing('new');
              setDraftName('');
            }}
            className="inline-flex items-center gap-1 rounded-full border border-dashed border-line-strong px-2 py-0.5 text-xs text-content-3 hover:text-content"
          >
            <Plus size={10} aria-hidden="true" />
            {t('assets.sheet.newLoadout', 'New Loadout')}
          </button>
        )
      )}

      {busy && <Loader2 size={12} className="animate-spin text-content-4" aria-hidden="true" />}
    </div>
  );
};

interface NameFieldProps {
  value: string;
  label: string;
  onChange: (next: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}

const NameField: React.FC<NameFieldProps> = ({
  value,
  label,
  onChange,
  onSubmit,
  onCancel,
}) => {
  // `label` already arrives translated (the caller holds the loadout name);
  // the cancel affordance is icon-only, so its accessible name is the ONLY
  // text a screen reader gets for it and has to come from the locale too.
  const { t } = useTranslation();
  return (
  <span className="inline-flex items-center gap-1 rounded-full border border-accent bg-card px-2 py-0.5">
    <input
      autoFocus
      aria-label={label}
      data-testid="loadout-name-input"
      value={value}
      maxLength={80}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onSubmit();
        if (e.key === 'Escape') onCancel();
      }}
      className="w-28 bg-transparent text-xs text-content outline-none"
    />
    <button
      type="button"
      data-testid="loadout-name-save"
      aria-label={label}
      onClick={onSubmit}
      className="text-content-3 hover:text-ok"
    >
      <Check size={11} aria-hidden="true" />
    </button>
    <button
      type="button"
      onClick={onCancel}
      aria-label={t('common.cancel', 'Cancel')}
      className="text-content-4 hover:text-content-2"
    >
      <X size={11} aria-hidden="true" />
    </button>
  </span>
  );
};
