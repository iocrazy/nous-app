import React, { useState, useCallback } from 'react';
import { ImageIcon, Loader2 } from 'lucide-react';

// ─── Types ────────────────────────────────────────────────────────────────────

interface NodeImagePreviewProps {
  imageUrl?: string;
  alt?: string;
  progress?: number; // 0-100, shows overlay when defined
  className?: string;
}

// ─── Component ────────────────────────────────────────────────────────────────

const NodeImagePreview = React.memo(function NodeImagePreview({
  imageUrl,
  alt = 'Node image',
  progress,
  className = '',
}: NodeImagePreviewProps) {
  const [viewerOpen, setViewerOpen] = useState(false);
  const [imgError, setImgError] = useState(false);

  const isGenerating = progress !== undefined && progress < 100;

  const handleClick = useCallback(() => {
    if (imageUrl && !isGenerating) {
      setViewerOpen(true);
    }
  }, [imageUrl, isGenerating]);

  const handleImgError = useCallback(() => {
    setImgError(true);
  }, []);

  return (
    <>
      <div
        className={[
          'relative rounded-lg overflow-hidden bg-gray-800 border border-gray-700',
          'flex items-center justify-center min-h-[120px]',
          imageUrl && !imgError && !isGenerating ? 'cursor-zoom-in' : '',
          className,
        ].join(' ')}
        onClick={handleClick}
      >
        {imageUrl && !imgError ? (
          <img
            src={imageUrl}
            alt={alt}
            className="w-full h-full object-cover"
            onError={handleImgError}
          />
        ) : (
          <div className="flex flex-col items-center gap-2 text-gray-600 py-4">
            <ImageIcon size={32} strokeWidth={1} />
            <span className="text-xs">No image</span>
          </div>
        )}

        {/* Generation progress overlay */}
        {isGenerating && (
          <div className="absolute inset-0 bg-gray-900/80 flex flex-col items-center justify-center gap-2">
            <Loader2 size={24} className="text-blue-400 animate-spin" />
            <div className="w-3/4 h-1.5 bg-gray-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="text-xs text-gray-300">{progress}%</span>
          </div>
        )}
      </div>

      {/* Image viewer modal (placeholder) */}
      {viewerOpen && imageUrl && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center"
          onClick={() => setViewerOpen(false)}
        >
          <img
            src={imageUrl}
            alt={alt}
            className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
          <button
            className="absolute top-4 right-4 text-white text-2xl hover:text-gray-300 transition-colors"
            onClick={() => setViewerOpen(false)}
          >
            ✕
          </button>
        </div>
      )}
    </>
  );
});

export default NodeImagePreview;
