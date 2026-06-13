import { useState, useEffect } from 'react';
import { useLocation, useParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { ResourceDetailPage } from '../components/ResourceDetailPage';
import { DownloadDetailPage } from './DownloadDetailPage';
import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';
import type { Video } from '../types';

/**
 * Unified resource detail dispatcher.
 * Routes: /resources/file/{resourceId}
 *
 * Detects the resource's source_type:
 * - 'web' (downloaded from platform) → DownloadDetailPage (download detail view)
 * - other (uploaded/imported) → ResourceDetailPage (resource management view)
 *
 * Optimization: when navigated from a card click (DownloadsView /
 * search), the caller passes the full ParsedMedia row via router state
 * as ``location.state.preloaded``. We use it to (a) skip the
 * source_type round-trip — search hits are always ``source_type='web'``
 * — and (b) hand DownloadDetailPage an immediate skeleton so the user
 * sees the card chrome rendered before any network call returns. This
 * is the same pattern most video sites (YouTube / Bilibili / TikTok)
 * use to avoid the blank-screen pause on search→detail.
 */
export function FileDetailDispatcher() {
  const { resourceId } = useParams<{ resourceId: string }>();
  const location = useLocation();
  const preloaded = (location.state as { preloaded?: Video } | null)?.preloaded;
  const [sourceType, setSourceType] = useState<string | null>(
    preloaded ? 'web' : null,
  );
  const [mediaId, setMediaId] = useState<string | null>(
    preloaded?.id ? String(preloaded.id) : null,
  );
  const [loading, setLoading] = useState(!preloaded);

  useEffect(() => {
    if (!resourceId) return;
    // Skip source_type round-trip when the navigation came with a
    // preloaded ParsedMedia — it's always source_type='web' for search
    // / DownloadsView card clicks, and we already have the media_id.
    if (preloaded?.id) return;

    const fetchSourceType = async () => {
      const supabase = getSupabaseClient();
      if (!isSupabaseConfigured() || !supabase) {
        setSourceType('unknown');
        setLoading(false);
        return;
      }

      try {
        const { data } = await supabase
          .from('resources')
          .select('source_type, media_id')
          .eq('id', resourceId)
          .single();

        setSourceType(data?.source_type || 'unknown');
        setMediaId(data?.media_id ? String(data.media_id) : null);
      } catch {
        setSourceType('unknown');
      } finally {
        setLoading(false);
      }
    };

    fetchSourceType();
  }, [resourceId, preloaded?.id]);

  if (!resourceId) return null;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-ink-500" />
      </div>
    );
  }

  // Downloaded from platform → DownloadDetailPage (download detail view).
  // `key={resourceId}` forces a full remount when navigating between two detail
  // pages (same `/resources/file/:resourceId` route, different param) — without
  // it React reuses the instance, so mount-only effects (the iOS reflow-kick)
  // never re-run and the inner scroller keeps the previous page's scrollTop,
  // which is exactly why the 2nd-and-later detail opened "jammed at the top".
  if (sourceType === 'web' && mediaId) {
    return (
      <DownloadDetailPage
        key={resourceId}
        resourceId={resourceId}
        mediaId={mediaId}
        preloaded={preloaded}
      />
    );
  }

  // Uploaded/imported → ResourceDetailPage (resource management view)
  return <ResourceDetailPage key={resourceId} resourceId={resourceId} />;
}
