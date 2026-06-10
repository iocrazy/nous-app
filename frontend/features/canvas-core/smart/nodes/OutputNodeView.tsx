import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useCallback, useState } from 'react';

import { CropEditorModal } from '../../editor/CropEditorModal';
import { FULL_REGION, type CropRegion } from '../../editor/types';
import type { OutputNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useNodeDataPatch } from './useNodeDataPatch';

const KIND_LABEL: Record<OutputNodeData['kind'], string> = {
  text: 'Text',
  image: 'Image',
  video: 'Video',
  audio: 'Audio',
};

export function OutputNodeView({ id, data, selected }: NodeProps) {
  const { kind, resource_id, preview_text, preview_url, crop_region } =
    data as unknown as OutputNodeData;
  const patchData = useNodeDataPatch(id);
  const [editorOpen, setEditorOpen] = useState(false);

  const canCrop = kind === 'image' && !!preview_url;

  const openEditor = useCallback(() => {
    if (canCrop) setEditorOpen(true);
  }, [canCrop]);

  const closeEditor = useCallback(() => {
    setEditorOpen(false);
  }, []);

  const handleCommit = useCallback(
    (region: CropRegion) => {
      patchData({ crop_region: region });
      setEditorOpen(false);
    },
    [patchData],
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
        />
      )}
    </div>
  );
}
