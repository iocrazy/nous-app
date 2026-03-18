import React, { useState, useCallback } from 'react';
import { NodeProps, Position } from '@xyflow/react';
import { ImageIcon, Info } from 'lucide-react';
import NodeWrapper from './shared/NodeWrapper';
import ImageViewerModal from '../shared/ImageViewerModal';
import { formatDimensionsWithRatio } from '../shared/imageUtils';

// ─── ImageNode ───────────────────────────────────────────────────────────────
// Passive display node — shows the output of generation/upload.
// Other nodes connect to it to pass image results downstream.

const ImageNode = React.memo(function ImageNode({ id, selected, data }: NodeProps) {
  const [viewerOpen, setViewerOpen] = useState(false);
  const [imgError, setImgError] = useState(false);

  const nodeData = data as Record<string, unknown>;
  const imageUrl = nodeData.image_url as string | undefined;
  const previewUrl = nodeData.preview_url as string | undefined;
  const width = nodeData.width as number | undefined;
  const height = nodeData.height as number | undefined;
  const fileName = nodeData.file_name as string | undefined;
  const locked = nodeData.locked as boolean | undefined;

  const displayUrl = previewUrl || imageUrl;
  const hasImage = Boolean(displayUrl) && !imgError;

  const handleDoubleClick = useCallback(() => {
    if (imageUrl) setViewerOpen(true);
  }, [imageUrl]);

  const dimensionText =
    width && height ? formatDimensionsWithRatio(width, height) : undefined;

  return (
    <NodeWrapper
      nodeId={id}
      title={fileName || 'Image'}
      icon={<ImageIcon size={14} />}
      selected={selected}
      locked={locked}
      accentColor="#10b981"
      handles={[
        { type: 'target', position: Position.Left },
        { type: 'source', position: Position.Right },
      ]}
      imageUrl={imageUrl}
    >
      {/* Image or placeholder */}
      <div
        className="relative rounded-lg overflow-hidden bg-gray-800 border border-gray-700 flex items-center justify-center min-h-[120px] cursor-zoom-in"
        onDoubleClick={handleDoubleClick}
      >
        {hasImage ? (
          <img
            src={displayUrl}
            alt={fileName || 'Image'}
            className="w-full h-full object-cover"
            onError={() => setImgError(true)}
            draggable={false}
          />
        ) : (
          <div className="flex flex-col items-center gap-2 text-gray-600 py-6">
            <ImageIcon size={32} strokeWidth={1} />
            <span className="text-xs">No image</span>
          </div>
        )}
      </div>

      {/* Footer info */}
      {dimensionText && (
        <div className="flex items-center gap-1 px-1 text-[10px] text-gray-500">
          <Info size={10} className="flex-shrink-0" />
          <span className="truncate">{dimensionText}</span>
        </div>
      )}

      {/* Image viewer modal */}
      {viewerOpen && imageUrl && (
        <ImageViewerModal
          images={[{ url: imageUrl, width, height }]}
          onClose={() => setViewerOpen(false)}
        />
      )}
    </NodeWrapper>
  );
});

export default ImageNode;
