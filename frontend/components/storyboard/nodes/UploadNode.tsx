import React, { useCallback, useState } from 'react';
import { NodeProps, Position } from '@xyflow/react';
import { Upload, ImageIcon } from 'lucide-react';
import NodeWrapper from './shared/NodeWrapper';
import NodeImagePreview from './shared/NodeImagePreview';
import { useStoryboardStore } from '../../../stores/storyboardStore';

// ─── UploadNode ───────────────────────────────────────────────────────────────

const UploadNode = React.memo(function UploadNode({ id, selected, data }: NodeProps) {
  const { updateNodeData } = useStoryboardStore();
  const [dragOver, setDragOver] = useState(false);

  const imageUrl = data?.imageUrl as string | undefined;
  const locked = data?.locked as boolean | undefined;

  const processFile = useCallback(
    (file: File) => {
      if (!file.type.startsWith('image/')) return;
      const url = URL.createObjectURL(file);
      updateNodeData(id, {
        data_json: { ...(data as Record<string, unknown>), imageUrl: url, fileName: file.name },
      });
    },
    [id, data, updateNodeData]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) processFile(file);
    },
    [processFile]
  );

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) processFile(file);
    },
    [processFile]
  );

  return (
    <NodeWrapper
      nodeId={id}
      title="Upload Image"
      icon={<Upload size={14} />}
      selected={selected}
      locked={locked}
      accentColor="#3b82f6"
      handles={[{ type: 'source', position: Position.Right }]}
    >
      {imageUrl ? (
        <NodeImagePreview imageUrl={imageUrl} alt="Uploaded image" />
      ) : (
        <label
          className={[
            'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed',
            'py-6 px-4 cursor-pointer transition-colors',
            dragOver
              ? 'border-blue-400 bg-blue-500/10'
              : 'border-gray-600 hover:border-gray-500 hover:bg-gray-800/50',
          ].join(' ')}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          <ImageIcon size={28} className="text-gray-500" strokeWidth={1} />
          <div className="text-center">
            <p className="text-sm text-gray-300 font-medium">Drop image here</p>
            <p className="text-xs text-gray-500 mt-0.5">or click to browse</p>
          </div>
          <input
            type="file"
            accept="image/*"
            className="hidden"
            onChange={handleFileChange}
          />
        </label>
      )}

      {imageUrl && (
        <button
          onClick={() => updateNodeData(id, { data_json: {} })}
          className="w-full text-xs text-gray-400 hover:text-red-400 transition-colors mt-1"
        >
          Remove image
        </button>
      )}
    </NodeWrapper>
  );
});

export default UploadNode;
