import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useCallback, useState } from 'react';

import { getResourceFileUrl } from '../../../../services/resourceService';
import { getSupabaseClient } from '../../../../supabaseClient';
import { CropEditorModal } from '../../editor/CropEditorModal';
import { FULL_REGION, type CropRegion } from '../../editor/types';
import { deriveCrop } from '../../services/canvasService';
import type { OutputNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useNodeDataPatch } from './useNodeDataPatch';

const KIND_LABEL: Record<OutputNodeData['kind'], string> = {
  text: 'Text',
  image: 'Image',
  video: 'Video',
  audio: 'Audio',
};

/** Build the served-file URL for a freshly-derived resource. The crop
 *  endpoint returns a resource row but no URL — the front-end composes
 *  it the same way ResourceCard does. */
async function buildPreviewUrl(resourceId: string): Promise<string> {
  try {
    const supabase = getSupabaseClient();
    const { data } = await supabase.auth.getSession();
    return getResourceFileUrl(resourceId, data.session?.access_token);
  } catch {
    // If we somehow can't read the session, fall back to the unsigned
    // URL — the <img> request will still carry the cookie auth if any.
    return getResourceFileUrl(resourceId);
  }
}

export function OutputNodeView({ id, data, selected }: NodeProps) {
  const { kind, resource_id, preview_text, preview_url, crop_region } =
    data as unknown as OutputNodeData;
  const patchData = useNodeDataPatch(id);
  const [editorOpen, setEditorOpen] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);

  const canCrop = kind === 'image' && !!preview_url;

  const openEditor = useCallback(() => {
    if (!canCrop) return;
    setCommitError(null);
    setEditorOpen(true);
  }, [canCrop]);

  const closeEditor = useCallback(() => {
    setEditorOpen(false);
    setCommitError(null);
  }, []);

  const handleCommit = useCallback(
    async (region: CropRegion) => {
      // Fallback path: no resource_id means the image was supplied
      // ad-hoc (no backend record). Persist the region locally so the
      // visual stays correct; nothing to derive against.
      if (!resource_id) {
        patchData({ crop_region: region });
        setEditorOpen(false);
        return;
      }
      try {
        setCommitting(true);
        setCommitError(null);
        const result = await deriveCrop(resource_id, region);
        const newId = String(result.id);
        const newUrl = await buildPreviewUrl(newId);
        patchData({
          resource_id: newId,
          preview_url: newUrl,
          // The new resource IS the cropped image — clear the in-node
          // crop so a second crop starts from a clean rectangle.
          crop_region: null,
        });
        setEditorOpen(false);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Failed to derive crop';
        setCommitError(message);
        // Leave the modal open so the user can retry or cancel.
      } finally {
        setCommitting(false);
      }
    },
    [resource_id, patchData],
  );

  return (
    <div
      data-testid="smart-output-node"
      className={`rounded-md border-2 bg-white shadow dark:bg-slate-900 ${
        selected
          ? 'border-indigo-500'
          : 'border-slate-300 dark:border-slate-700'
      }`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.output }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !bg-slate-400"
      />
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1.5 dark:border-slate-700">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Output · {KIND_LABEL[kind]}
        </div>
        <div className="flex items-center gap-2">
          {crop_region && (
            <div
              data-testid="crop-region-badge"
              className="text-[10px] uppercase tracking-wider text-indigo-600"
              title="Crop applied"
            >
              Cropped
            </div>
          )}
          {resource_id && (
            <div className="text-[10px] uppercase tracking-wider text-emerald-600">
              Saved
            </div>
          )}
        </div>
      </div>
      <div
        data-testid="smart-output-body"
        onDoubleClick={openEditor}
        className={`p-3 ${canCrop ? 'cursor-zoom-in' : ''}`}
        title={canCrop ? 'Double-click to crop' : undefined}
      >
        {kind === 'image' && preview_url ? (
          <img
            src={preview_url}
            alt={preview_text || 'Output preview'}
            draggable={false}
            className="block w-full rounded object-contain"
          />
        ) : preview_text ? (
          <div className="line-clamp-4 whitespace-pre-wrap text-sm text-slate-800 dark:text-slate-200">
            {preview_text}
          </div>
        ) : (
          <div className="text-xs italic text-slate-400">
            {kind === 'text' ? 'No text yet' : `No ${kind} rendered yet`}
          </div>
        )}
      </div>
      {canCrop && preview_url && (
        <CropEditorModal
          open={editorOpen}
          src={preview_url}
          alt={preview_text || 'Output preview'}
          initialRegion={crop_region ?? FULL_REGION}
          onCommit={handleCommit}
          onCancel={closeEditor}
          committing={committing}
        />
      )}
      {commitError && editorOpen && (
        <div
          data-testid="crop-commit-error"
          role="alert"
          className="absolute left-1/2 top-1/2 z-[51] mt-32 -translate-x-1/2 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {commitError}
        </div>
      )}
    </div>
  );
}
