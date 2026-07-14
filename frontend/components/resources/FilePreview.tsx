// frontend/components/resources/FilePreview.tsx
// Extracted from ResourceDetailPage.tsx (Task 8, spec 2026-07-14): renders the
// right preview surface for a resource's detail page — text resources route
// to TextResourcePreview (in-place TipTap editor), everything else keeps the
// existing image/video/audio/pdf/download branches.
import React, { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, File, FileText, Film, Image, Music } from 'lucide-react';
import type { Resource } from '../../types';
import { getResourceCoverUrl, setResourceChorus, uploadResourceCover } from '../../services/resourceService';
import { useToast } from '../Toast';
import { AudioHero } from '../AudioHero';
import { TextResourcePreview } from './TextResourcePreview';
import { classifyTextResource } from '../../utils/textResourceMode';

function getFileIcon(mimeType: string | null | undefined) {
  if (!mimeType) return { icon: File, color: 'text-ink-400', bg: 'bg-ink-500/20' };
  if (mimeType.startsWith('video/')) return { icon: Film, color: 'text-purple-400', bg: 'bg-purple-500/20' };
  if (mimeType.startsWith('image/')) return { icon: Image, color: 'text-green-400', bg: 'bg-green-500/20' };
  if (mimeType.startsWith('audio/')) return { icon: Music, color: 'text-orange-400', bg: 'bg-orange-500/20' };
  if (mimeType.startsWith('text/') || mimeType.includes('pdf') || mimeType.includes('document'))
    return { icon: FileText, color: 'text-blue-400', bg: 'bg-blue-500/20' };
  return { icon: File, color: 'text-ink-400', bg: 'bg-ink-500/20' };
}

export const FilePreview: React.FC<{
  resource: Resource;
  fileUrl: string | null;
  currentUserId?: string;
  onCoverUpdated?: (updated: Resource) => void;
}> = ({ resource, fileUrl, currentUserId, onCoverUpdated }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const mime = resource.mime_type || '';
  const coverInputRef = useRef<HTMLInputElement>(null);

  const handleCoverFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = ''; // allow re-selecting the same file
    if (!f) return;
    try {
      const updated = await uploadResourceCover(resource.id, f);
      onCoverUpdated?.(updated);
      addToast(t('resources.detail.coverUpdated', 'Cover updated'), 'success');
    } catch (err) {
      console.error('Failed to upload cover:', err);
      addToast('Failed to upload cover', 'error');
    }
  };

  if (!fileUrl) {
    const { icon: IconComponent, color } = getFileIcon(mime);
    return (
      <div className="flex flex-col items-center gap-4 text-center">
        <IconComponent size={64} className={`${color} opacity-60`} />
        <p className="text-content-2 text-sm">{resource.filename}</p>
        <p className="text-content-3 text-xs">{t('resources.noPreview')}</p>
      </div>
    );
  }

  const textMode = classifyTextResource({
    filename: resource.filename,
    mime: resource.mime_type,
    sizeBytes: resource.file_size_bytes ?? null,
  });
  if (fileUrl && textMode !== null) {
    const canEdit =
      resource.source_type === 'upload' &&
      !!currentUserId &&
      String(resource.creator_id) === String(currentUserId);
    return (
      <TextResourcePreview
        resource={resource}
        fileUrl={fileUrl}
        canEdit={canEdit}
      />
    );
  }

  if (mime.startsWith('video/')) {
    return (
      <video
        src={fileUrl}
        controls
        className="max-w-full max-h-[calc(100vh-13rem)] rounded-lg object-contain"
        poster={resource.thumbnail_path || undefined}
      />
    );
  }

  if (mime.startsWith('audio/')) {
    const isUpload = resource.source_type === 'upload';
    return (
      <div className="w-full h-full">
        <AudioHero
          src={fileUrl}
          title={resource.filename}
          coverUrl={
            resource.cover_image_path && resource.id
              ? getResourceCoverUrl(String(resource.id), undefined, resource.updated_at)
              : resource.thumbnail_path || undefined
          }
          duration={resource.duration_seconds ?? undefined}
          onCoverClick={isUpload ? () => coverInputRef.current?.click() : undefined}
          chorusStartSec={
            resource.chorus_start_ms != null ? resource.chorus_start_ms / 1000 : undefined
          }
          chorusEditable={isUpload}
          setChorusLabel={t('resources.detail.setChorus', 'Set chorus')}
          clearChorusLabel={t('resources.detail.clearChorus', 'Clear')}
          onChorusChange={
            isUpload
              ? async (sec) => {
                  try {
                    const ms = sec == null ? null : Math.round(sec * 1000);
                    const updated = await setResourceChorus(resource.id, ms);
                    onCoverUpdated?.(updated);
                    addToast(
                      t(
                        ms == null
                          ? 'resources.detail.chorusCleared'
                          : 'resources.detail.chorusSet',
                        ms == null ? 'Chorus removed' : 'Chorus marked',
                      ),
                      'success',
                    );
                  } catch (err) {
                    console.error('Failed to set chorus:', err);
                    addToast('Failed to set chorus', 'error');
                  }
                }
              : undefined
          }
        />
        {isUpload && (
          <input
            ref={coverInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={handleCoverFile}
          />
        )}
      </div>
    );
  }

  if (mime.startsWith('image/')) {
    return (
      <img
        src={fileUrl}
        alt={resource.filename}
        className="max-w-full max-h-[calc(100vh-13rem)] object-contain rounded-lg"
      />
    );
  }

  if (mime === 'application/pdf') {
    return (
      <iframe
        src={fileUrl}
        className="w-full h-[calc(100vh-13rem)] rounded-lg"
        title={resource.filename}
      />
    );
  }

  // Default: icon + download
  const { icon: IconComponent, color } = getFileIcon(mime);
  return (
    <div className="flex flex-col items-center gap-4 text-center">
      <IconComponent size={64} className={`${color} opacity-60`} />
      <p className="text-content-2 font-medium">{resource.filename}</p>
      <p className="text-content-3 text-sm">{t('resources.noPreview')}</p>
      <a
        href={fileUrl}
        download
        className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm transition-colors"
      >
        <Download size={16} />
        {t('resources.download')}
      </a>
    </div>
  );
};
