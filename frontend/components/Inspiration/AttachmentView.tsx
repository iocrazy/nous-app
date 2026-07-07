// Renders a note's attachments by mime family (spec §2.2 #5):
// image grid / inline audio / video card / typed download chip.
import React from 'react';
import { FileText } from 'lucide-react';
import {
  attachmentUrl,
  type NoteAttachment,
} from '../../services/inspirationService';

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function extOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toUpperCase().slice(0, 5) : 'FILE';
}

export const AttachmentView: React.FC<{ attachments: NoteAttachment[] }> = ({
  attachments,
}) => {
  if (!attachments.length) return null;
  const images = attachments.filter((a) => a.mime.startsWith('image/'));
  const audios = attachments.filter((a) => a.mime.startsWith('audio/'));
  const videos = attachments.filter((a) => a.mime.startsWith('video/'));
  const files = attachments.filter(
    (a) =>
      !a.mime.startsWith('image/') &&
      !a.mime.startsWith('audio/') &&
      !a.mime.startsWith('video/'),
  );

  return (
    <div className="mt-2 space-y-2">
      {images.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {images.map((a) => (
            <a
              key={a.id}
              href={attachmentUrl(a.id)}
              target="_blank"
              rel="noreferrer"
              className="block"
            >
              <img
                src={attachmentUrl(a.id)}
                alt={a.original_name}
                loading="lazy"
                className="h-24 w-32 rounded-lg object-cover bg-island-2"
              />
            </a>
          ))}
        </div>
      )}
      {videos.map((a) => (
        <video
          key={a.id}
          src={attachmentUrl(a.id)}
          controls
          preload="metadata"
          className="max-h-64 rounded-lg bg-island-2"
        />
      ))}
      {audios.map((a) => (
        <audio key={a.id} src={attachmentUrl(a.id)} controls className="h-9 w-full max-w-xs" />
      ))}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {files.map((a) => (
            <a
              key={a.id}
              href={attachmentUrl(a.id)}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-lg bg-island-2 px-3 py-1.5 text-xs text-content-2 hover:bg-line"
            >
              <FileText size={14} className="text-content-3" />
              <span className="max-w-[180px] truncate">{a.original_name}</span>
              <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[10px] font-bold text-indigo-300">
                {extOf(a.original_name)}
              </span>
              <span className="text-content-3">{formatSize(a.size_bytes)}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
};
