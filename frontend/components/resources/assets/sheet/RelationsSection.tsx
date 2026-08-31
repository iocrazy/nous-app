// frontend/components/resources/assets/sheet/RelationsSection.tsx
//
// The Wears / Holds / Worn By / Held By / Attached To blocks (spec 6.2).
//
// The direction is the whole design. An OUTGOING section owns its rows: this
// character wears these costumes, and removing one is this page's business. An
// INCOMING section is a view of rows that live on the other asset - it lists
// them and links to them, but offers no Remove, because the link is not this
// sheet's to delete. Rendering an identical control on both sides would put a
// delete button on a row this page cannot honestly speak for.
//
// A blocked Add (an audio asset with no subtype) renders the reason, not a
// disabled button with no explanation: the server's answer would be 422
// `link_not_allowed`, and saying it up front is the same answer, earlier.

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, X } from 'lucide-react';

import type { AssetRow, AssetRowDetail } from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import { ASSET_TYPE_ICON } from '../assetTypeMeta';
import { linkRowsFor, type RelationSectionSpec } from './assetSheetModel';
import { LinkAssetDialog } from './LinkAssetDialog';

export interface RelationsSectionProps {
  scopeId: string;
  detail: AssetRowDetail;
  spec: RelationSectionSpec;
  /** id -> row for every linked asset the sheet resolved. A miss renders the
   *  raw id: an asset we could not name is still an asset that is linked. */
  related: Record<string, AssetRow | undefined>;
  readOnly: boolean;
  onOpenAsset: (assetId: string) => void;
  onAdd: (toAssetId: string) => void;
  onRemove: (toAssetId: string, relation: RelationSectionSpec['relations'][number]) => void;
  onError: (err: unknown) => void;
}

export const RelationsSection: React.FC<RelationsSectionProps> = ({
  scopeId,
  detail,
  spec,
  related,
  readOnly,
  onOpenAsset,
  onAdd,
  onRemove,
  onError,
}) => {
  const { t } = useTranslation();
  const [picking, setPicking] = useState(false);

  const rows = linkRowsFor(detail, spec);
  const canAdd = !readOnly && spec.direction === 'outgoing' && spec.targetType !== null;

  return (
    <section
      data-testid="relation-section"
      data-relation-key={spec.key}
      data-direction={spec.direction}
      className="flex flex-col gap-2"
    >
      <div className="flex items-center gap-2">
        <h3 className="text-[13px] font-medium text-content-2">
          {t(`assets.rel.${spec.key}`, spec.key)}
        </h3>
        <span className="text-[11px] tabular-nums text-content-4">{rows.length}</span>
        <div className="flex-1" />
        {canAdd && (
          <button
            type="button"
            data-testid="relation-add"
            onClick={() => setPicking(true)}
            className="inline-flex items-center gap-1 rounded-lg border border-line-strong px-2 py-0.5 text-xs font-medium text-content-2 hover:bg-island-2"
          >
            <Plus size={11} aria-hidden="true" />
            {t('assets.rel.addAction', 'Add')}
          </button>
        )}
      </div>

      {/* Not a disabled button: a control with no explanation reads as a bug.
          The section still lists whatever links already exist. */}
      {!readOnly && spec.addBlockedReason && (
        <p data-testid="relation-add-blocked" className="text-[11px] text-warn">
          {t(
            `assets.rel.blocked.${spec.addBlockedReason}`,
            'Set an audio subtype before linking this asset',
          )}
        </p>
      )}

      {rows.length === 0 ? (
        <p className="text-[12px] text-content-4">{t('assets.rel.empty', 'Nothing Linked Yet')}</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {rows.map(({ link, otherId }) => {
            const other = related[otherId];
            const Icon = other ? ASSET_TYPE_ICON[other.asset_type] : null;
            return (
              <li
                key={`${link.relation}:${otherId}`}
                data-testid="relation-row"
                data-asset-id={otherId}
                className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-card py-1 pl-1 pr-1.5"
              >
                <button
                  type="button"
                  onClick={() => onOpenAsset(otherId)}
                  className="flex items-center gap-1.5"
                >
                  <span className="h-7 w-7 overflow-hidden rounded border border-line bg-island-2">
                    {other?.cover_file_id ? (
                      <img
                        src={getResourceCoverUrl(other.cover_file_id)}
                        alt=""
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <span className="flex h-full w-full items-center justify-center text-content-4">
                        {Icon ? <Icon size={12} /> : null}
                      </span>
                    )}
                  </span>
                  <span className="max-w-[9rem] truncate text-[12px] text-content">
                    {other?.name ?? otherId}
                  </span>
                </button>
                {/* Outgoing only: an incoming row belongs to the other asset. */}
                {!readOnly && spec.direction === 'outgoing' && (
                  <button
                    type="button"
                    data-testid="relation-remove"
                    aria-label={t('assets.rel.remove', {
                      name: other?.name ?? otherId,
                      defaultValue: 'Unlink {{name}}',
                    })}
                    onClick={() => onRemove(otherId, link.relation)}
                    className="text-content-4 hover:text-danger"
                  >
                    <X size={11} aria-hidden="true" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {picking && spec.targetType && spec.addRelation && (
        <LinkAssetDialog
          open
          scopeId={scopeId}
          targetType={spec.targetType}
          relation={spec.addRelation}
          selfId={detail.id}
          existingIds={rows.map((r) => r.otherId)}
          onClose={() => setPicking(false)}
          onPick={(row) => {
            setPicking(false);
            onAdd(row.id);
          }}
          onError={onError}
        />
      )}
    </section>
  );
};
