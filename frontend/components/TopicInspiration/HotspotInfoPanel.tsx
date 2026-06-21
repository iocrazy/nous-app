// frontend/components/TopicInspiration/HotspotInfoPanel.tsx
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { FileText, Download, ExternalLink } from 'lucide-react';
import { useIslandWork } from '../../contexts/IslandWorkContext';
import { useToast } from '../Toast';
import { generateScript, parseDownload, type Hotspot } from '../../services/topicService';

export const HotspotInfoPanel: React.FC<{ hotspot: Hotspot | null }> = ({ hotspot }) => {
  const { t } = useTranslation();
  const { infoIslandEl, setInfoAvailable, setInfoVisible } = useIslandWork();
  const { addToast } = useToast();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setInfoAvailable(!!hotspot);
    if (hotspot && infoIslandEl) {
      setInfoVisible(true);
    } else {
      setInfoVisible(false);
    }
  }, [hotspot, infoIslandEl, setInfoAvailable, setInfoVisible]);

  if (!hotspot || !infoIslandEl) return null;

  const onGenerate = async () => {
    setBusy(true);
    try {
      await generateScript(hotspot.id);
      addToast(t('topic.scriptStarted', 'Script generation started'), 'success');
    } catch (e) {
      addToast(`${(e as Error).message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const onParse = async () => {
    if (!hotspot.media_url) return;
    setBusy(true);
    try {
      await parseDownload(hotspot.media_url);
      addToast(t('topic.downloadStarted', 'Download dispatched'), 'success');
    } catch (e) {
      addToast(`${(e as Error).message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const btn =
    'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold transition-colors';

  return createPortal(
    <div className="p-4 space-y-3">
      <h3 className="text-sm font-semibold text-content">{hotspot.title}</h3>

      {hotspot.reason && (
        <div className="rounded-lg border border-amber-500/35 bg-amber-500/[0.12] px-3 py-2 text-xs text-[var(--amb-tx,#b45309)]">
          <div className="font-semibold mb-0.5">{t('topic.reason', 'Reason')}</div>
          {hotspot.reason}
        </div>
      )}

      {(hotspot.ai_summary || hotspot.summary) && (
        <div className="rounded-lg border border-indigo-500/35 bg-indigo-500/[0.12] px-3 py-2 text-xs text-[var(--ind-tx,#4338ca)]">
          <div className="font-semibold mb-0.5">{t('topic.aiSummary', 'AI Summary')}</div>
          {hotspot.ai_summary || hotspot.summary}
        </div>
      )}

      <div className="space-y-2 pt-1">
        <button
          disabled={busy}
          onClick={onGenerate}
          className={`${btn} bg-indigo-500/[0.12] text-[var(--ind-tx,#4338ca)] border border-indigo-500/35`}
        >
          <FileText size={14} />
          {t('topic.generateScript', 'Generate Script')}
        </button>

        {hotspot.media_url && (
          <button
            disabled={busy}
            onClick={onParse}
            className={`${btn} bg-amber-500/[0.12] text-[var(--amb-tx,#b45309)] border border-amber-500/35`}
          >
            <Download size={14} />
            {t('topic.parseDownload', 'Parse & Download')}
          </button>
        )}

        {hotspot.origin_url && (
          <a
            href={hotspot.origin_url}
            target="_blank"
            rel="noreferrer"
            className={`${btn} bg-island-2 text-content-2 border border-line`}
          >
            <ExternalLink size={14} />
            {t('topic.viewSource', 'View Source')}
          </a>
        )}
      </div>

      <div className="pt-2 border-t border-line text-xs text-content-3 space-y-1">
        {hotspot.source_label && (
          <div>
            {t('topic.source', 'Source')}: {hotspot.source_label}
          </div>
        )}
        {typeof hotspot.score === 'number' && (
          <div>
            {t('topic.score', 'Score')}: {Math.round(hotspot.score * 100)}
          </div>
        )}
        {hotspot.captured_at && (
          <div>{hotspot.captured_at.slice(0, 16).replace('T', ' ')}</div>
        )}
      </div>
    </div>,
    infoIslandEl,
  );
};
