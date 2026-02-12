import React from 'react';
import { Video, FileText, Image, File, Clock } from 'lucide-react';
import { ProjectFile } from '../types';

interface FileCardProps {
  file: ProjectFile;
  onClick: () => void;
  viewMode: 'grid' | 'list';
}

const getFileIcon = (fileType: string | null) => {
  if (!fileType) return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
  const ft = fileType.toLowerCase();
  if (ft === 'video' || ft.startsWith('video')) return { icon: Video, color: 'text-purple-400', bg: 'bg-purple-500/20' };
  if (ft === 'image' || ft.startsWith('image')) return { icon: Image, color: 'text-green-400', bg: 'bg-green-500/20' };
  if (ft === 'document' || ft === 'pdf' || ft === 'text') return { icon: FileText, color: 'text-blue-400', bg: 'bg-blue-500/20' };
  return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
};

const formatFileSize = (bytes: number | null): string => {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

const formatDate = (dateStr: string): string => {
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
};

export const FileCard: React.FC<FileCardProps> = ({ file, onClick, viewMode }) => {
  const { icon: IconComponent, color, bg } = getFileIcon(file.file_type);

  if (viewMode === 'list') {
    return (
      <div
        onClick={onClick}
        className="flex items-center gap-4 px-4 py-3 bg-zinc-800/60 hover:bg-zinc-800 border border-zinc-700/30 hover:border-zinc-600 rounded-xl cursor-pointer transition-all duration-200 group"
      >
        <div className={`p-2 rounded-lg ${bg} flex-shrink-0`}>
          <IconComponent size={18} className={color} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm text-white truncate group-hover:text-indigo-300 transition-colors">
            {file.filename}
          </p>
        </div>
        <div className="text-xs text-zinc-500 flex-shrink-0">
          {formatFileSize(file.file_size_bytes)}
        </div>
        <div className="text-xs text-zinc-500 flex-shrink-0 flex items-center gap-1">
          <Clock size={12} />
          {formatDate(file.created_at)}
        </div>
      </div>
    );
  }

  return (
    <div
      onClick={onClick}
      className="bg-zinc-800/80 hover:bg-zinc-800 border border-zinc-700/50 hover:border-zinc-600 rounded-xl cursor-pointer transition-all duration-200 group overflow-hidden"
    >
      {/* Thumbnail area */}
      <div className={`h-32 flex items-center justify-center ${bg}`}>
        <IconComponent size={40} className={`${color} opacity-60 group-hover:opacity-100 transition-opacity`} />
      </div>
      {/* Info */}
      <div className="p-3">
        <p className="text-sm text-white truncate group-hover:text-indigo-300 transition-colors font-medium">
          {file.filename}
        </p>
        <div className="flex items-center justify-between mt-2 text-xs text-zinc-500">
          <span>{formatFileSize(file.file_size_bytes)}</span>
          <span>{formatDate(file.created_at)}</span>
        </div>
      </div>
    </div>
  );
};
