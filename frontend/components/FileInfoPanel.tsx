import React from 'react';
import { X, Video, FileText, Image, File } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ProjectFile } from '../types';

interface FileInfoPanelProps {
  file: ProjectFile;
  onClose: () => void;
}

const formatFileSize = (bytes: number | null): string => {
  if (!bytes) return 'Unknown';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

const formatDuration = (seconds: number | null): string => {
  if (!seconds) return 'Unknown';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
};

const formatBitrate = (kbps: number | null): string => {
  if (!kbps) return 'Unknown';
  if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Mbps`;
  return `${kbps} Kbps`;
};

const formatSampleRate = (rate: number | null): string => {
  if (!rate) return 'Unknown';
  if (rate >= 1000) return `${(rate / 1000).toFixed(1)} kHz`;
  return `${rate} Hz`;
};

const getFileIcon = (fileType: string | null) => {
  if (!fileType) return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
  const ft = fileType.toLowerCase();
  if (ft === 'video' || ft.startsWith('video')) return { icon: Video, color: 'text-purple-400', bg: 'bg-purple-500/20' };
  if (ft === 'image' || ft.startsWith('image')) return { icon: Image, color: 'text-green-400', bg: 'bg-green-500/20' };
  if (ft === 'document' || ft === 'pdf' || ft === 'text') return { icon: FileText, color: 'text-blue-400', bg: 'bg-blue-500/20' };
  return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
};

const formatDate = (dateStr: string): string => {
  return new Date(dateStr).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const InfoRow = ({ label, value }: { label: string; value: string | null | undefined }) => {
  if (!value) return null;
  return (
    <div className="flex justify-between items-center py-2 border-b border-zinc-800/50">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="text-sm text-zinc-300">{value}</span>
    </div>
  );
};

const isVideoFile = (fileType: string | null): boolean => {
  if (!fileType) return false;
  const ft = fileType.toLowerCase();
  return ft === 'video' || ft.startsWith('video');
};

export const FileInfoPanel: React.FC<FileInfoPanelProps> = ({ file, onClose }) => {
  const { t } = useTranslation();
  const { icon: IconComponent, color, bg } = getFileIcon(file.file_type);
  const showVideoInfo = isVideoFile(file.file_type);

  return (
    <div className="w-80 bg-zinc-900 border-l border-zinc-800 h-full overflow-y-auto animate-in slide-in-from-right-4 duration-300">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-zinc-800 sticky top-0 bg-zinc-900 z-10">
        <h3 className="text-sm font-semibold text-white">{t('mediatrack.fileInfo')}</h3>
        <button
          onClick={onClose}
          className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      {/* Preview */}
      <div className={`mx-4 mt-4 h-40 rounded-xl flex items-center justify-center ${bg}`}>
        <IconComponent size={48} className={`${color} opacity-70`} />
      </div>

      {/* Basic Info */}
      <div className="px-4 mt-4">
        <h4 className="text-sm font-medium text-white mb-1 break-words">{file.filename}</h4>
        <div className="space-y-0">
          <InfoRow label={t('mediatrack.fileType')} value={file.file_type || file.mime_type} />
          <InfoRow label={t('mediatrack.size')} value={formatFileSize(file.file_size_bytes)} />
          <InfoRow label={t('mediatrack.createdAt')} value={formatDate(file.created_at)} />
          <InfoRow label={t('mediatrack.updatedAt')} value={formatDate(file.updated_at)} />
        </div>
      </div>

      {/* Video Stream */}
      {showVideoInfo && (file.resolution || file.fps || file.video_codec || file.video_bitrate_kbps || file.duration_seconds) && (
        <div className="px-4 mt-5">
          <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
            {t('mediatrack.videoStream')}
          </h4>
          <div className="space-y-0">
            <InfoRow label={t('mediatrack.duration')} value={formatDuration(file.duration_seconds)} />
            <InfoRow label={t('mediatrack.resolution')} value={file.resolution?.replace(/:/g, 'x')} />
            <InfoRow label={t('mediatrack.fps')} value={file.fps ? `${file.fps} fps` : null} />
            <InfoRow label={t('mediatrack.codec')} value={file.video_codec} />
            <InfoRow label={t('mediatrack.bitrate')} value={formatBitrate(file.video_bitrate_kbps)} />
          </div>
        </div>
      )}

      {/* Audio Stream */}
      {showVideoInfo && (file.audio_codec || file.audio_channels || file.audio_sample_rate || file.audio_bitrate_kbps) && (
        <div className="px-4 mt-5">
          <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
            {t('mediatrack.audioStream')}
          </h4>
          <div className="space-y-0">
            <InfoRow label={t('mediatrack.codec')} value={file.audio_codec} />
            <InfoRow label={t('mediatrack.channels')} value={file.audio_channels ? `${file.audio_channels}` : null} />
            <InfoRow label={t('mediatrack.sampleRate')} value={formatSampleRate(file.audio_sample_rate)} />
            <InfoRow label={t('mediatrack.bitrate')} value={formatBitrate(file.audio_bitrate_kbps)} />
          </div>
        </div>
      )}

      {/* Notes */}
      {file.notes && (
        <div className="px-4 mt-5 mb-4">
          <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
            {t('mediatrack.notes')}
          </h4>
          <p className="text-sm text-zinc-300 whitespace-pre-wrap">{file.notes}</p>
        </div>
      )}

      {/* Bottom spacing */}
      <div className="h-6" />
    </div>
  );
};
