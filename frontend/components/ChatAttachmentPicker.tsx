/**
 * B — Chat attachment picker.
 *
 * File picker + upload + preview chips. Used above ChatInput in
 * AIChatPanel. Manages its own staging state — the parent receives
 * the resolved AttachmentRequest[] via onChange.
 *
 * Files are uploaded eagerly on pick (24h TTL on server). Removing a
 * chip drops it from the staged list (no server-side cleanup — the TTL
 * sweeper handles that).
 *
 * Library resources (@ picker / "Send to Agent") stage in the SAME wrapping
 * row rather than as chips inside the sentence, so one glance answers "what
 * is going with this message?". They are optional props: the other consumer
 * of this component (Todolist/IssueReplyBox) keeps its own inline chips and
 * passes neither.
 */
import React, { useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Paperclip, X, Image as ImageIcon, Film, FileText, Loader2 } from 'lucide-react';
import {
  ACCEPT_ATTR,
  formatBytes as _formatBytes,
} from './ChatAttachmentPicker.helpers';
import { useChatAttachmentUpload } from '../hooks/useChatAttachmentUpload';
import { ResourceChipBody } from './chat/ResourceChipBody';
import { AssetChipBody } from './chat/AssetChipBody';
import { StagedAssetLoadoutMenu } from './chat/StagedAssetLoadoutMenu';
import {
  removeStagedAsset,
  removeStagedResource,
  setStagedAssetLoadout,
  type StagedAssetRef,
  type StagedResourceRef,
} from './chat/stagedResources';

export interface StagedAttachment {
  kind: 'image' | 'video' | 'pdf';
  url: string;
  filename: string;
  size_bytes: number;
  mime: string | null;
  /** Temp resource id from the upload — sent with the message so history
      reloads can render the image via the resource cover endpoint. */
  resource_id?: string;
  /** Local data URL for image previews (we keep it client-side; not sent). */
  preview_data_url?: string;
}

interface ChatAttachmentPickerProps {
  attachments: StagedAttachment[];
  onChange: (next: StagedAttachment[]) => void;
  disabled?: boolean;
  /** Library resources waiting to be sent with this turn. */
  resources?: StagedResourceRef[];
  onResourcesChange?: (next: StagedResourceRef[]) => void;
  /** Library ASSETS waiting to be sent with this turn (P5). Their own list,
   *  not folded into `resources`: they resolve through a different backend
   *  path and carry a different snapshot. */
  assets?: StagedAssetRef[];
  onAssetsChange?: (next: StagedAssetRef[]) => void;
}

function _kindIcon(kind: StagedAttachment['kind']): React.ReactNode {
  if (kind === 'image') return <ImageIcon className="w-3 h-3" />;
  if (kind === 'video') return <Film className="w-3 h-3" />;
  return <FileText className="w-3 h-3" />;
}

export const ChatAttachmentPicker: React.FC<ChatAttachmentPickerProps> = ({
  attachments,
  onChange,
  disabled = false,
  resources = [],
  onResourcesChange,
  assets = [],
  onAssetsChange,
}) => {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);

  // Delegate all upload logic to the shared hook; pass t() so validation
  // error keys are translated (preserves prior i18n behaviour).
  const { handleFiles: _handleFiles, uploading } = useChatAttachmentUpload({
    attachments,
    onChange,
    translateError: t,
  });

  const handlePick = useCallback(() => {
    inputRef.current?.click();
  }, []);

  const handleFiles = useCallback(
    async (fileList: FileList | null) => {
      await _handleFiles(fileList);
      // Reset the input so re-picking the same file fires onChange
      if (inputRef.current) inputRef.current.value = '';
    },
    [_handleFiles],
  );

  const removeAt = useCallback(
    (idx: number) => {
      onChange(attachments.filter((_, i) => i !== idx));
    },
    [attachments, onChange],
  );

  const removeResource = useCallback(
    (resourceId: string) => {
      onResourcesChange?.(removeStagedResource(resources, resourceId));
    },
    [resources, onResourcesChange],
  );

  const removeAsset = useCallback(
    (assetId: string) => {
      onAssetsChange?.(removeStagedAsset(assets, assetId));
    },
    [assets, onAssetsChange],
  );

  // The v2 loadout pick. The picker owns no staged state of its own — it
  // reports the whole next list upward, exactly as removal does, so the two
  // edits to one chip cannot end up with different owners.
  const changeAssetLoadout = useCallback(
    (assetId: string, loadoutId: string | null, loadoutName: string | null) => {
      onAssetsChange?.(
        setStagedAssetLoadout(assets, assetId, loadoutId, loadoutName),
      );
    },
    [assets, onAssetsChange],
  );

  const isDisabled = disabled || uploading;

  // Nothing staged at all + no upload → just the picker button (anchored to
  // ChatInput layout). Staged resources OR assets alone must keep the row:
  // they are the only thing telling the user the item was picked up.
  if (
    attachments.length === 0
    && resources.length === 0
    && assets.length === 0
    && !uploading
  ) {
    return (
      <button
        type="button"
        onClick={handlePick}
        disabled={isDisabled}
        title={t('chat.attachments.attachTooltip')}
        className="flex-shrink-0 p-1.5 rounded-md text-ink-500 hover:text-ink-300 hover:bg-ink-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <Paperclip className="w-4 h-4" />
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTR}
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </button>
    );
  }

  // With anything staged → chip strip + add-more button. Resources and
  // files share one wrapping row, so a long list flows onto a second line
  // instead of pushing the composer around.
  return (
    <div data-testid="composer-attachment-row" className="flex items-center gap-1 flex-wrap min-h-[28px]">
      {resources.map((r) => (
        <ResourceChipBody
          key={r.resource_id}
          variant="staged"
          resourceId={r.resource_id}
          name={r.name}
          kind={r.kind}
          mime={r.mime}
          thumbnailUrl={r.thumbnail_url}
          transcriptStatus={r.transcript_status}
          summaryStatus={r.summary_status}
          onRemove={() => removeResource(r.resource_id)}
        />
      ))}

      {assets.map((a) => (
        <AssetChipBody
          key={a.asset_id}
          assetId={a.asset_id}
          name={a.name}
          assetType={a.asset_type}
          coverFileId={a.cover_file_id}
          loadoutName={a.loadout_name}
          loadoutMenu={(
            <StagedAssetLoadoutMenu
              asset={a}
              onChange={(loadoutId, loadoutName) =>
                changeAssetLoadout(a.asset_id, loadoutId, loadoutName)}
            />
          )}
          onRemove={() => removeAsset(a.asset_id)}
        />
      ))}

      {attachments.map((a, idx) => (
        <div
          key={`${a.url}-${idx}`}
          className="group flex items-center gap-1.5 px-2 py-0.5 bg-ink-800 border border-ink-700 rounded text-[11px] text-ink-300"
          title={`${a.filename} · ${_formatBytes(a.size_bytes)}`}
        >
          {a.preview_data_url ? (
            <img
              src={a.preview_data_url}
              alt=""
              className="w-4 h-4 object-cover rounded"
            />
          ) : (
            _kindIcon(a.kind)
          )}
          <span className="truncate max-w-[120px]">{a.filename}</span>
          <button
            type="button"
            onClick={() => removeAt(idx)}
            disabled={isDisabled}
            className="text-ink-500 hover:text-red-400 transition-colors"
            title={t('chat.attachments.remove')}
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      ))}

      <button
        type="button"
        onClick={handlePick}
        disabled={isDisabled}
        title={t('chat.attachments.attachMore')}
        className="flex-shrink-0 p-1 rounded text-ink-500 hover:text-ink-300 hover:bg-ink-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {uploading ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <Paperclip className="w-3.5 h-3.5" />
        )}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTR}
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </button>
    </div>
  );
};

export default ChatAttachmentPicker;
