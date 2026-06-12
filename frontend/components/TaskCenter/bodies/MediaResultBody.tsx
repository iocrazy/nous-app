import React from 'react';
import { ExternalLink, Download } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../../contexts/AuthContext';
import type { UnifiedTask } from '../../../contexts/TaskManagerContext';
import {
  getResourceCoverUrl,
  getResourceFileUrl,
  getResourceMediaUrl,
} from '../../../services/resourceService';
import { AudioWaveformPlayer } from '../../AudioWaveformPlayer';

interface MediaResultBodyProps {
  task: UnifiedTask;
  /** Resource shape from fetchResourceById (loosely typed — read defensively). */
  resource: unknown;
  onOpenResource: (resourceId: string) => void;
}

export const MediaResultBody: React.FC<MediaResultBodyProps> = ({
  task,
  resource,
  onOpenResource,
}) => {
  const { t } = useTranslation();
  const { mediaToken } = useAuth();
  const rid = String(task.resource_id);
  const r = (resource ?? {}) as {
    filename?: string;
    mime_type?: string | null;
    duration_seconds?: number | null;
    resolution?: string | null;
    file_size_bytes?: number | null;
  };
  const isAudio = (r.mime_type ?? '').startsWith('audio/');

  return (
    <div className="p-4 space-y-4">
      {isAudio ? (
        <AudioWaveformPlayer
          src={getResourceMediaUrl(rid, mediaToken ?? undefined)}
          filename={r.filename || task.title}
          duration={r.duration_seconds ?? undefined}
        />
      ) : (
        <img
          src={getResourceCoverUrl(rid)}
          alt=""
          className="w-full max-h-72 object-contain rounded-lg bg-ink-950"
        />
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        {r.filename && <Row label={t('topbar.resFilename')} value={r.filename} />}
        {r.resolution && <Row label={t('topbar.resResolution')} value={r.resolution} />}
        {r.duration_seconds != null && (
          <Row label={t('topbar.resDuration')} value={`${Math.round(r.duration_seconds)}s`} />
        )}
        {r.file_size_bytes != null && (
          <Row label={t('topbar.resSize')} value={`${(r.file_size_bytes / 1e6).toFixed(1)} MB`} />
        )}
      </dl>

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={() => onOpenResource(rid)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs text-indigo-300 bg-indigo-500/15 hover:bg-indigo-500/25 transition-colors"
        >
          <ExternalLink size={13} /> {t('topbar.openResource')}
        </button>
        <a
          href={getResourceFileUrl(rid, mediaToken ?? undefined)}
          download
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs text-ink-300 bg-ink-800 hover:bg-ink-700 transition-colors"
        >
          <Download size={13} /> {t('topbar.downloadResult')}
        </a>
      </div>
    </div>
  );
};

const Row: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="flex flex-col">
    <dt className="text-[10px] uppercase tracking-wide text-ink-500">{label}</dt>
    <dd className="text-ink-200 truncate">{value}</dd>
  </div>
);
