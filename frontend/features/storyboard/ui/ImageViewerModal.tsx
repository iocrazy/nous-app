import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, Loader2, RotateCcw, X } from 'lucide-react';
import { useImageViewerTransform } from '../hooks/useImageViewerTransform';

const UI_CONTENT_OVERLAY_INSET_CLASS = 'inset-0';

export interface ImageViewerModalProps {
  open: boolean;
  imageUrl: string;
  imageList: string[];
  currentIndex: number;
  onClose: () => void;
  onNavigate: (direction: 'prev' | 'next') => void;
}

export function ImageViewerModal({
  open,
  imageUrl,
  imageList,
  currentIndex,
  onClose,
  onNavigate,
}: ImageViewerModalProps): React.ReactElement | null {
  const { t } = useTranslation();
  const viewerControlClass =
    'inline-flex h-10 items-center justify-center rounded-full border border-white/20 bg-black/60 px-4 text-sm text-white backdrop-blur-xl';
  const [isVisible, setIsVisible] = useState(false);
  const [overlayOpacity, setOverlayOpacity] = useState(0);
  const [displayImageUrl, setDisplayImageUrl] = useState(imageUrl);
  const [isImageLoaded, setIsImageLoaded] = useState(false);
  const closeTimerRef = useRef<number | null>(null);

  const {
    containerRef,
    imageRef,
    scaleDisplayRef,
    viewerOpacity,
    resetView,
    handleImageMouseDown,
    handleContainerMouseMove,
    handleContainerMouseUp,
    handleImageMouseMove,
    handleImageLoad,
    isPointOnImageContent,
  } = useImageViewerTransform(open && isVisible);

  const onImageLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      setIsImageLoaded(true);
      handleImageLoad(e);
    },
    [handleImageLoad],
  );

  useEffect(() => {
    if (!isVisible) return;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [isVisible]);

  useEffect(() => {
    if (open) {
      setDisplayImageUrl(imageUrl);
      setIsVisible(true);
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
      setOverlayOpacity(0);
      requestAnimationFrame(() => {
        setOverlayOpacity(1);
      });
      return;
    }
    if (!isVisible) return;
    setOverlayOpacity(0);
    closeTimerRef.current = window.setTimeout(() => {
      setIsVisible(false);
      setDisplayImageUrl('');
    }, 400);
    return () => {
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
    };
  }, [open, isVisible]);

  useEffect(() => {
    if (!open || !imageUrl) {
      return;
    }
    setIsImageLoaded(false);
    setDisplayImageUrl(imageUrl);
  }, [open, imageUrl]);

  // Prefetch adjacent images
  useEffect(() => {
    if (!open || imageList.length <= 1) return;
    const adjacentIndices = [currentIndex - 1, currentIndex + 1];
    adjacentIndices.forEach((idx) => {
      if (idx >= 0 && idx < imageList.length) {
        const img = new Image();
        img.src = imageList[idx];
      }
    });
  }, [open, currentIndex, imageList]);

  useEffect(() => {
    return () => {
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    resetView();
  }, [open, imageUrl, resetView]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        onNavigate('prev');
      } else if (e.key === 'ArrowRight') {
        onNavigate('next');
      } else if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onNavigate, onClose]);

  if (!isVisible) return null;

  return (
    <div
      className={`fixed ${UI_CONTENT_OVERLAY_INSET_CLASS} z-[100] overflow-hidden bg-black/90 backdrop-blur-lg`}
      style={{
        opacity: overlayOpacity,
        transition: 'opacity 400ms ease',
        pointerEvents: open ? 'auto' : 'none',
      }}
    >
      <div
        ref={containerRef}
        className="absolute inset-0 flex items-center justify-center overflow-hidden p-4"
        style={{ overscrollBehavior: 'contain' }}
        onMouseMove={handleContainerMouseMove}
        onMouseUp={handleContainerMouseUp}
        onMouseLeave={handleContainerMouseUp}
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div className="relative">
          {!isImageLoaded && displayImageUrl && (
            <div className="absolute inset-0 z-10 flex items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-white/70" />
            </div>
          )}
          <img
            ref={imageRef}
            src={displayImageUrl}
            alt={t('viewer.imageAlt', 'Image')}
            className="select-none transition-opacity duration-300"
            style={{
              opacity: isImageLoaded ? viewerOpacity * overlayOpacity : 0,
              transformOrigin: 'center',
              width: '95vw',
              height: '95vh',
              objectFit: 'contain',
            }}
            onLoad={onImageLoad}
            onMouseDown={handleImageMouseDown}
            onMouseMove={handleImageMouseMove}
            onClick={(e) => {
              if (isPointOnImageContent(e.clientX, e.clientY)) {
                e.stopPropagation();
              } else {
                onClose();
              }
            }}
            draggable={false}
          />
        </div>

        <div className="absolute bottom-8 left-1/2 flex -translate-x-1/2 flex-col items-center gap-3">
          {imageList.length > 1 && (
            <div className="flex items-center gap-3">
              <button
                onClick={() => onNavigate('prev')}
                disabled={currentIndex <= 0}
                className="rounded-full bg-zinc-800/80 p-2 text-white backdrop-blur-sm transition-all duration-200 hover:bg-zinc-700/80 disabled:cursor-not-allowed disabled:opacity-50"
                title={t('viewer.prev', 'Previous')}
              >
                <ChevronLeft className="h-5 w-5" />
              </button>
              <button
                onClick={() => onNavigate('next')}
                disabled={currentIndex >= imageList.length - 1}
                className="rounded-full bg-zinc-800/80 p-2 text-white backdrop-blur-sm transition-all duration-200 hover:bg-zinc-700/80 disabled:cursor-not-allowed disabled:opacity-50"
                title={t('viewer.next', 'Next')}
              >
                <ChevronRight className="h-5 w-5" />
              </button>
            </div>
          )}

          <div className="flex items-center gap-4">
            {imageList.length > 1 && (
              <div className={viewerControlClass}>
                {currentIndex + 1} / {imageList.length}
              </div>
            )}
            <div
              ref={scaleDisplayRef}
              className={`${viewerControlClass} min-w-[74px]`}
            >
              100%
            </div>
            <button
              onClick={resetView}
              className={`${viewerControlClass} transition-colors hover:bg-white/10`}
              title={t('viewer.reset', 'Reset View')}
            >
              <RotateCcw className="h-4 w-4" />
            </button>
            <button
              onClick={onClose}
              className={`${viewerControlClass} transition-colors hover:bg-white/10`}
              title={t('common.close', 'Close')}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
