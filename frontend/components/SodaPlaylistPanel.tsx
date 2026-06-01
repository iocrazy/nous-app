import React, { useState } from 'react';
import { Loader2, Music, Download, ListMusic } from 'lucide-react';
import { useToast } from './Toast';
import {
  getSodaPlaylist,
  downloadSodaTracks,
  SodaTrackSummary,
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

  const handleLoad = async () => {
    if (!url.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getSodaPlaylist(url.trim());
      setTracks(result.tracks);
      setSelected(new Set(result.tracks.map((t) => t.track_id)));
      setLoaded(true);
    } catch (err) {
      console.error('Failed to load Soda playlist:', err);
      const message = err instanceof Error ? err.message : 'Failed to load playlist';
      setError(message);
      setTracks([]);
      setSelected(new Set());
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
      const result = await downloadSodaTracks([...selected], undefined);
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
        <div className="relative bg-zinc-900 rounded-xl p-2 border border-zinc-800 shadow-xl flex items-center">
          <ListMusic className="ml-3 text-zinc-500 w-5 h-5 flex-shrink-0" />
          <input
            type="text"
            placeholder="Paste a Soda Music playlist link"
            className="flex-1 bg-transparent border-none outline-none text-zinc-200 placeholder-zinc-600 px-4 py-3"
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
        <div className="bg-zinc-900/50 border border-zinc-800 rounded-xl p-6 text-center text-zinc-500 text-sm">
          No music tracks in this playlist
        </div>
      )}

      {/* Track checklist */}
      {tracks.length > 0 && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
          {/* Header row */}
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
            <button
              type="button"
              onClick={allSelected ? deselectAll : selectAll}
              className="text-xs font-medium text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              {allSelected ? 'Deselect all' : 'Select all'}
            </button>
            <span className="text-xs text-zinc-500">({selected.size} selected)</span>
          </div>

          {/* Rows */}
          <div className="max-h-96 overflow-y-auto divide-y divide-zinc-800/50">
            {tracks.map((track) => {
              const checked = selected.has(track.track_id);
              const duration = formatDuration(track.duration_ms);
              return (
                <label
                  key={track.track_id}
                  className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-zinc-800/40 transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleTrack(track.track_id)}
                    className="w-4 h-4 rounded accent-indigo-600 flex-shrink-0"
                  />
                  <div className="w-10 h-10 rounded bg-zinc-800 flex items-center justify-center flex-shrink-0 overflow-hidden">
                    {track.cover_url ? (
                      <img
                        src={track.cover_url}
                        alt=""
                        className="w-full h-full object-cover"
                        onError={(e) => {
                          (e.currentTarget as HTMLImageElement).style.display = 'none';
                        }}
                      />
                    ) : (
                      <Music size={16} className="text-zinc-600" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-zinc-200 truncate">
                      {track.title || 'Untitled'}
                    </div>
                    {track.artist && (
                      <div className="text-xs text-zinc-500 truncate">{track.artist}</div>
                    )}
                  </div>
                  {duration && (
                    <span className="text-xs text-zinc-500 font-mono flex-shrink-0">
                      {duration}
                    </span>
                  )}
                </label>
              );
            })}
          </div>

          {/* Download footer */}
          <div className="flex justify-end px-4 py-3 border-t border-zinc-800">
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
