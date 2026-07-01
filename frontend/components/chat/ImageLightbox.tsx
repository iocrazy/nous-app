import React, { useEffect } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface ImageLightboxProps {
  src: string;
  alt?: string;
  onClose: () => void;
}

export function ImageLightbox({
  src,
  alt,
  onClose,
}: ImageLightboxProps): React.ReactElement {
  const { t } = useTranslation();

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] bg-black/80 flex items-center justify-center px-4 py-6"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <button
        type="button"
        title={t('chat.image.close')}
        aria-label={t('chat.image.close')}
        onClick={onClose}
        className="absolute right-4 top-4 w-9 h-9 rounded-[8px] grid place-items-center bg-black/50 border border-white/20 text-white hover:bg-black/70 transition-colors"
      >
        <X size={18} />
      </button>
      <img
        src={src}
        alt={alt ?? ''}
        className="max-w-[92vw] max-h-[92vh] object-contain"
        onClick={(event) => event.stopPropagation()}
      />
    </div>
  );
}
