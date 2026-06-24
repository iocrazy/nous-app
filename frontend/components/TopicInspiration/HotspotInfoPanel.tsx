// frontend/components/TopicInspiration/HotspotInfoPanel.tsx
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { FileText, Download, ExternalLink, Star, EyeOff } from 'lucide-react';
import { useIslandWork } from '../../contexts/IslandWorkContext';
import { useToast } from '../Toast';
import {
  generateScript,
  parseDownload,
  getHotspot,
  type Hotspot,
} from '../../services/topicService';

const CONTENT_PREVIEW_CHARS = 400;

interface Props {
  hotspot: Hotspot | null;
  onToggleSave?: (h: Hotspot) => void;
  onToggleHide?: (h: Hotspot) => void;
}

export const HotspotInfoPanel: React.FC<Props> = ({
  hotspot,
  onToggleSave,
  onToggleHide,
}) => {
  const { t } = useTranslation();
  const { infoIslandEl, setInfoAvailable, setInfoVisible } = useIslandWork();
  const { addToast } = useToast();
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<Hotspot | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setInfoAvailable(!!hotspot);
    setInfoVisible(!!(hotspot && infoIslandEl));
  }, [hotspot, infoIslandEl, setInfoAvailable, setInfoVisible]);

  // Fetch the full record (original body) when a different hotspot is opened.
  useEffect(() => {
    let alive = true;
    setExpanded(false);
    setDetail(null);
    if (!hotspot) return;
    (async () => {
      try {
        const full = await getHotspot(hotspot.id);
        if (alive) setDetail(full);
      } catch (err) {
        // Non-fatal: panel still shows the list-row fields.
        console.error('hotspot detail load failed', err);
      }
    })();
    return () => {
      alive = false;
    };
  }, [hotspot]);

  if (!hotspot || !infoIslandEl) return null;

  // Content/text come from the detail fetch when available; live state flags
  // come from the prop so card↔panel stay in sync after a toggle.
  const h = detail ?? hotspot;
  const content = h.content_original || h.content_translated || '';
  const longContent = content.length > CONTENT_PREVIEW_CHARS;
  const shownContent =
    longContent && !expanded ? `${content.slice(0, CONTENT_PREVIEW_CHARS)}…` : content;

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
    if (!h.media_url) return;
    setBusy(true);
    try {
      await parseDownload(h.media_url);
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
      {/* Header: title + save/hide toggles */}
      <div className="flex items-start gap-2">
        <h3 className="text-sm font-semibold text-content flex-1 min-w-0">{h.title}</h3>
        <div className="flex items-center gap-1 shrink-0">
          {onToggleSave && (
            <button
              type="button"
              aria-label={t('topic.save', 'Save')}
              title={t('topic.save', 'Save')}
              onClick={() => onToggleSave(hotspot)}
              className="p-1 rounded-md hover:bg-island-2 text-content-4"
            >
              <Star
                size={16}
                className={hotspot.is_saved ? 'text-amber-500' : ''}
                fill={hotspot.is_saved ? 'currentColor' : 'none'}
              />
            </button>
          )}
          {onToggleHide && (
            <button
              type="button"
              aria-label={hotspot.is_hidden ? t('topic.unhide', 'Unhide') : t('topic.hide', 'Hide')}
              title={hotspot.is_hidden ? t('topic.unhide', 'Unhide') : t('topic.hide', 'Hide')}
              onClick={() => onToggleHide(hotspot)}
              className="p-1 rounded-md hover:bg-island-2 text-content-4"
            >
              <EyeOff size={16} className={hotspot.is_hidden ? 'text-accent' : ''} />
            </button>
          )}
        </div>
      </div>

      {/* Category + tags */}
      {(h.category || (h.tags?.length ?? 0) > 0) && (
        <div className="flex flex-wrap items-center gap-1">
          {h.category && (
            <span className="text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full bg-accent/10 text-accent">
              {h.category}
            </span>
          )}
          {(h.tags ?? []).map((tag) => (
            <span
              key={tag}
              className="text-[10px] px-2 py-0.5 rounded-full bg-island-2 text-content-3"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {h.reason && (
        <div className="rounded-lg border border-amber-500/35 bg-amber-500/[0.12] px-3 py-2 text-xs text-[var(--amb-tx,#b45309)]">
          <div className="font-semibold mb-0.5">{t('topic.reason', 'Why it matters')}</div>
          {h.reason}
        </div>
      )}

      {(h.ai_summary || h.summary) && (
        <div className="rounded-lg border border-indigo-500/35 bg-indigo-500/[0.12] px-3 py-2 text-xs text-[var(--ind-tx,#4338ca)]">
          <div className="font-semibold mb-0.5">{t('topic.aiSummary', 'AI Summary')}</div>
          {h.ai_summary || h.summary}
        </div>
      )}

      {/* Original content */}
      {content && (
        <div className="rounded-lg border border-line bg-island-2 px-3 py-2">
          <div className="font-semibold mb-1 text-[11px] uppercase tracking-wide text-content-3">
            {t('topic.original', 'Original')}
          </div>
          <p className="text-xs leading-relaxed text-content-2 whitespace-pre-wrap break-words">
            {shownContent}
          </p>
          {longContent && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="mt-1 text-[11px] font-semibold text-accent hover:underline"
            >
              {expanded ? t('topic.showLess', 'Show less') : t('topic.showMore', 'Show more')}
            </button>
          )}
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

        {h.media_url && (
          <button
            disabled={busy}
            onClick={onParse}
            className={`${btn} bg-amber-500/[0.12] text-[var(--amb-tx,#b45309)] border border-amber-500/35`}
          >
            <Download size={14} />
            {t('topic.parseDownload', 'Parse & Download')}
          </button>
        )}

        {(h.origin_url || h.url) && (
          <a
            href={h.origin_url || h.url || undefined}
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
        {h.source_label && (
          <div>
            {t('topic.source', 'Source')}: {h.source_label}
          </div>
        )}
        {typeof h.score === 'number' && (
          <div>
            {t('topic.score', 'Score')}: {Math.round(h.score * 100)}
          </div>
        )}
        {typeof h.source_count === 'number' && h.source_count > 1 && (
          <div className="text-[var(--ind-tx,#4338ca)] font-medium">
            {t('topic.seenOnPlatforms', {
              count: h.source_count,
              defaultValue: 'Seen on {{count}} platforms',
            })}
          </div>
        )}
        {h.captured_at && <div>{h.captured_at.slice(0, 16).replace('T', ' ')}</div>}
      </div>
    </div>,
    infoIslandEl,
  );
};
