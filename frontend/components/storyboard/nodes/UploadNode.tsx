import React, { useCallback, useRef, useState } from 'react';
import { NodeProps, Position } from '@xyflow/react';
import { Upload, ImageIcon, Loader2, AlertCircle } from 'lucide-react';
import NodeWrapper from './shared/NodeWrapper';
import NodeImagePreview from './shared/NodeImagePreview';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import { uploadImage } from '../../../services/storyboardService';

// ─── UploadNode ───────────────────────────────────────────────────────────────

const UploadNode = React.memo(function UploadNode({ id, selected, data }: NodeProps) {
  const { updateNodeData, currentProjectId } = useStoryboardStore();
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  const imageUrl = data?.imageUrl as string | undefined;
  const previewUrl = data?.previewUrl as string | undefined;
  const locked = data?.locked as boolean | undefined;

  const cleanupBlobUrl = useCallback(() => {
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  const processFile = useCallback(
    async (file: File) => {
      if (!file.type.startsWith('image/')) {
        setError('Please select an image file');
        return;
      }

      if (file.size > 20 * 1024 * 1024) {
        setError('File too large. Maximum size is 20 MB');
        return;
      }

      if (!currentProjectId) {
        setError('No project selected');
        return;
      }

      setError(null);
      setUploading(true);
      setUploadProgress(10);

      // Instant preview via blob URL
      cleanupBlobUrl();
      const blobUrl = URL.createObjectURL(file);
      blobUrlRef.current = blobUrl;

      updateNodeData(id, {
        data_json: {
          ...(data as Record<string, unknown>),
          imageUrl: blobUrl,
          fileName: file.name,
        },
      });

      setUploadProgress(30);

      try {
        const result = await uploadImage(currentProjectId, file, id);
        setUploadProgress(90);

        // Replace blob URL with server URL
        cleanupBlobUrl();
        updateNodeData(id, {
          data_json: {
            ...(data as Record<string, unknown>),
            imageUrl: result.image_url,
            previewUrl: result.preview_url,
            assetId: result.asset_id,
            width: result.width,
            height: result.height,
            fileHash: result.file_hash,
            fileName: file.name,
          },
        });

        setUploadProgress(100);
      } catch (err) {
        console.error('[UploadNode] Upload failed:', err);
        const message = err instanceof Error ? err.message : 'Upload failed';
        setError(message);

        // Revert to empty state on failure
        cleanupBlobUrl();
        updateNodeData(id, {
          data_json: {
            ...(data as Record<string, unknown>),
            imageUrl: undefined,
            previewUrl: undefined,
            fileName: undefined,
          },
        });
      } finally {
        setUploading(false);
        setUploadProgress(undefined);
      }
    },
    [id, data, updateNodeData, currentProjectId, cleanupBlobUrl],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) processFile(file);
    },
    [processFile],
  );

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) processFile(file);
    },
    [processFile],
  );

  const handleRemove = useCallback(() => {
    cleanupBlobUrl();
    updateNodeData(id, { data_json: {} });
    setError(null);
  }, [id, updateNodeData, cleanupBlobUrl]);

  // Use preview URL for the node display, full URL for the lightbox
  const displayUrl = previewUrl || imageUrl;

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
      {displayUrl && !uploading ? (
        <NodeImagePreview imageUrl={displayUrl} alt="Uploaded image" />
      ) : uploading ? (
        <div className="flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-blue-400 bg-blue-500/10 py-6 px-4">
          <Loader2 size={28} className="text-blue-400 animate-spin" />
          <p className="text-sm text-gray-300 font-medium">Uploading...</p>
          {uploadProgress !== undefined && (
            <div className="w-full h-1.5 bg-gray-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-300"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          )}
        </div>
      ) : (
        <label
          className={[
            'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed',
            'py-6 px-4 cursor-pointer transition-colors',
            dragOver
              ? 'border-blue-400 bg-blue-500/10'
              : 'border-gray-600 hover:border-gray-500 hover:bg-gray-800/50',
          ].join(' ')}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
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

      {error && (
        <div className="flex items-center gap-1.5 mt-1 px-1">
          <AlertCircle size={12} className="text-red-400 flex-shrink-0" />
          <p className="text-xs text-red-400 truncate">{error}</p>
        </div>
      )}

      {imageUrl && !uploading && (
        <button
          onClick={handleRemove}
          className="w-full text-xs text-gray-400 hover:text-red-400 transition-colors mt-1"
        >
          Remove image
        </button>
      )}
    </NodeWrapper>
  );
});

export default UploadNode;
