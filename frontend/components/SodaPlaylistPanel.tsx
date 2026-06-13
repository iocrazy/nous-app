import React, { useState } from 'react';
import { Loader2, Music, Download, ListMusic, Film, Check } from 'lucide-react';
import { useToast } from './Toast';
import {
  getSodaPlaylist,
  downloadSodaTracks,
  SodaTrackSummary,
  SodaDownloadItem,
} from '../services/parserService';

interface SodaPlaylistPanelProps {
  onSubmitted?: (flowId: string, submitted: number) => void;
}

/** Format milliseconds into m:ss; null → empty string. */
const formatDuration = (ms: number | null): string => {
  if (ms == null || ms <= 0) return '';
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
};

export function SodaPlaylistPanel({ onSubmitted }: SodaPlaylistPanelProps) {
  const { addToast } = useToast();
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [tracks, setTracks] = useState<SodaTrackSummary[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [downloadedCount, setDownloadedCount] = useState(0);
  const [newCount, setNewCount] = useState(0);

  const handleLoad = async () => {
    if (!url.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getSodaPlaylist(url.trim());
      setTracks(result.tracks);
      // Incremental sync: default-select only the tracks the user has NOT
      // downloaded yet. Downloaded ones stay selectable for a forced re-download.
      setSelected(
        new Set(result.tracks.filter((t) => !t.downloaded).map((t) => t.track_id)),
      );
      // Prefer the server counts; fall back to deriving from the track flags.
      const dc =
        result.downloaded_count ??
        result.tracks.filter((t) => t.downloaded).length;
      setDownloadedCount(dc);
      setNewCount(result.new_count ?? result.tracks.length - dc);
      setLoaded(true);
    } catch (err) {
      console.error('Failed to load Soda playlist:', err);
      const message = err instanceof Error ? err.message : 'Failed to load playlist';
      setError(message);
      setTracks([]);
      setSelected(new Set());
      setDownloadedCount(0);
      setNewCount(0);
      setLoaded(false);
    } finally {
      setLoading(false);
    }
  };

  const toggleTrack = (trackId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(trackId)) {
        next.delete(trackId);
      } else {
        next.add(trackId);
      }
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(tracks.map((t) => t.track_id)));
  const deselectAll = () => setSelected(new Set());

  const allSelected = tracks.length > 0 && selected.size === tracks.length;

  const handleDownload = async () => {
    if (selected.size === 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      // Build {id, kind} items by looking up each selected id's kind from the
      // loaded tracks (default "track" for older responses without a kind).
      const items: SodaDownloadItem[] = tracks
        .filter((t) => selected.has(t.track_id))
        .map((t) => ({ id: t.track_id, kind: t.kind ?? 'track' }));
      const result = await downloadSodaTracks(items, undefined);
      if (result.submitted === 0 || !result.success) {
        const message = 'Failed to submit tracks for download';
        setError(message);
        addToast(message, 'error');
        return;
      }
      addToast(`Submitted ${result.submitted} tracks to download`, 'success');
      onSubmitted?.(result.flow_id, result.submitted);
    } catch (err) {
      console.error('Failed to submit Soda tracks:', err);
      const message = err instanceof Error ? err.message : 'Failed to submit tracks';
      setError(message);
      addToast(message, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* URL input + Load button */}
      <div className="relative group">
        <div className="absolute -inset-0.5 bg-gradient-to-r from-indigo-500 to-purple-600 rounded-xl opacity-30 group-hover:opacity-60 transition duration-500 blur"></div>
        <div className="relative bg-ink-900 rounded-xl p-2 border border-ink-800 shadow-xl flex items-center">
          <ListMusic className="ml-3 text-ink-500 w-5 h-5 flex-shrink-0" />
          <input
            type="text"
            placeholder="Paste a Soda Music playlist link"
            className="flex-1 bg-transparent border-none outline-none text-ink-200 placeholder-ink-600 px-4 py-3"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleLoad()}
          />
          <button
            onClick={handleLoad}
            disabled={loading || !url.trim()}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-6 py-2.5 rounded-lg font-medium transition-all shadow-lg shadow-indigo-500/20 flex items-center gap-2 flex-shrink-0"
          >
            {loading ? <Loader2 className="animate-spin w-4 h-4" /> : 'Load playlist'}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-950/20 border border-red-900/50 text-red-200 p-3 rounded-xl text-sm">
          {error}
        </div>
      )}

      {/* Empty state — loaded but no tracks */}
      {loaded && tracks.length === 0 && !loading && (
        <div className="bg-ink-900/50 border border-ink-800 rounded-xl p-6 text-center text-ink-500 text-sm">
          No music tracks in this playlist
        </div>
      )}

      {/* Track checklist */}
      {tracks.length > 0 && (
        <div className="bg-ink-900 border border-ink-800 rounded-xl overflow-hidden">
          {/* Header row */}
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-ink-800">
            <button
              type="button"
              onClick={allSelected ? deselectAll : selectAll}
              className="text-xs font-medium text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              {allSelected ? 'Deselect all' : 'Select all'}
            </button>
            <span className="text-xs text-ink-500">
              {newCount} new · {downloadedCount} downloaded
            </span>
          </div>

          {/* Rows */}
          <div className="max-h-96 overflow-y-auto divide-y divide-ink-800/50">
            {tracks.map((track) => {
              const checked = selected.has(track.track_id);
              const duration = formatDuration(track.duration_ms);
              const isVideo = track.kind === 'video';
              const isDownloaded = track.downloaded === true;
              return (
                <label
                  key={track.track_id}
                  className={`flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-ink-800/40 transition-colors ${
                    isDownloaded ? 'opacity-50' : ''
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleTrack(track.track_id)}
                    className="w-4 h-4 rounded accent-indigo-600 flex-shrink-0"
                  />
                  <div className="w-10 h-10 rounded bg-ink-800 flex items-center justify-center flex-shrink-0 overflow-hidden">
                    {track.cover_url ? (
                      <img
                        src={track.cover_url}
                        alt=""
                        className="w-full h-full object-cover"
                        onError={(e) => {
                          (e.currentTarget as HTMLImageElement).style.display = 'none';
                        }}
                      />
                    ) : isVideo ? (
                      <Film size={16} className="text-ink-600" />
                    ) : (
                      <Music size={16} className="text-ink-600" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-sm text-ink-200 truncate">
                        {track.title || 'Untitled'}
                      </span>
                      {isVideo && (
                        <span className="flex items-center gap-1 flex-shrink-0 text-[10px] font-medium uppercase tracking-wide text-purple-300 bg-purple-500/15 border border-purple-500/30 rounded px-1.5 py-0.5">
                          <Film size={10} />
                          Video
                        </span>
                      )}
                      {isDownloaded && (
                        <span className="flex items-center gap-1 flex-shrink-0 text-[10px] font-medium uppercase tracking-wide text-emerald-300 bg-emerald-500/15 border border-emerald-500/30 rounded px-1.5 py-0.5">
                          <Check size={10} />
                          Downloaded
                        </span>
                      )}
                    </div>
                    {track.artist && (
                      <div className="text-xs text-ink-500 truncate">{track.artist}</div>
                    )}
                  </div>
                  {duration && (
                    <span className="text-xs text-ink-500 font-mono flex-shrink-0">
                      {duration}
                    </span>
                  )}
                </label>
              );
            })}
          </div>

          {/* Download footer */}
          <div className="flex justify-end px-4 py-3 border-t border-ink-800">
            <button
              onClick={handleDownload}
              disabled={selected.size === 0 || submitting}
              className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-6 py-2 rounded-lg font-medium transition-all shadow-lg shadow-indigo-500/20 flex items-center gap-2"
            >
              {submitting ? (
                <Loader2 className="animate-spin w-4 h-4" />
              ) : (
                <Download size={16} />
              )}
              Download selected ({selected.size})
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
