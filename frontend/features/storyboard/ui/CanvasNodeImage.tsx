import { memo, useCallback, useState, type ImgHTMLAttributes, type MouseEvent } from 'react';
import { Loader2 } from 'lucide-react';

import { useCanvasStore } from '../../../stores/canvasStore';

export interface CanvasNodeImageProps extends ImgHTMLAttributes<HTMLImageElement> {
  viewerSourceUrl?: string | null;
  viewerImageList?: Array<string | null | undefined>;
  disableViewer?: boolean;
}

function normalizeViewerList(
  imageList: Array<string | null | undefined> | undefined,
  currentImageUrl: string
): string[] {
  const deduped: string[] = [];
  for (const rawItem of imageList ?? []) {
    const item = typeof rawItem === 'string' ? rawItem.trim() : '';
    if (!item || deduped.includes(item)) {
      continue;
    }
    deduped.push(item);
  }

  if (!deduped.includes(currentImageUrl)) {
    deduped.unshift(currentImageUrl);
  }

  return deduped.length > 0 ? deduped : [currentImageUrl];
}

export const CanvasNodeImage = memo(({
  viewerSourceUrl,
  viewerImageList,
  disableViewer = false,
  onDoubleClick,
  src,
  className,
  ...props
}: CanvasNodeImageProps) => {
  const openImageViewer = useCanvasStore((state) => state.openImageViewer);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  const handleDoubleClick = useCallback((event: MouseEvent<HTMLImageElement>) => {
    onDoubleClick?.(event);

    if (event.defaultPrevented || disableViewer) {
      return;
    }

    const fallbackSrc = event.currentTarget.currentSrc || (typeof src === 'string' ? src : '');
    const resolvedSource =
      typeof viewerSourceUrl === 'string' && viewerSourceUrl.trim().length > 0
        ? viewerSourceUrl.trim()
        : fallbackSrc.trim();
    if (!resolvedSource) {
      return;
    }

    event.stopPropagation();
    openImageViewer(resolvedSource, normalizeViewerList(viewerImageList, resolvedSource));
  }, [disableViewer, onDoubleClick, openImageViewer, src, viewerImageList, viewerSourceUrl]);

  return (
    <div className="relative">
      {/* Loading spinner */}
      {!loaded && !error && src && (
        <div className="absolute inset-0 flex items-center justify-center bg-zinc-900/50">
          <Loader2 size={20} className="animate-spin text-zinc-500" />
        </div>
      )}
      <img
        {...props}
        src={src}
        loading="lazy"
        className={`${className ?? ''} transition-opacity duration-200 ${loaded ? 'opacity-100' : 'opacity-0'}`}
        data-viewer-src={
          typeof viewerSourceUrl === 'string' && viewerSourceUrl.trim().length > 0
            ? viewerSourceUrl.trim()
            : undefined
        }
        onDoubleClick={handleDoubleClick}
        onLoad={(e) => {
          setLoaded(true);
          props.onLoad?.(e);
        }}
        onError={(e) => {
          setError(true);
          setLoaded(true);
          props.onError?.(e);
        }}
      />
    </div>
  );
});

CanvasNodeImage.displayName = 'CanvasNodeImage';
