import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { ResourceDetail } from '../components/ResourceDetail';
import { PlayerPage } from './PlayerPage';
import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';

/**
 * Unified resource detail page.
 * Routes: /resources/file/{resourceId}
 *
 * Detects the resource's source_type:
 * - 'web' (downloaded from platform) → PlayerPage (download detail view)
 * - other (uploaded/imported) → ResourceDetail (resource management view)
 */
export function ResourceDetailPage() {
  const { resourceId } = useParams<{ resourceId: string }>();
  const [sourceType, setSourceType] = useState<string | null>(null);
  const [mediaId, setMediaId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!resourceId) return;

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
  }, [resourceId]);

  if (!resourceId) return null;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-zinc-500" />
      </div>
    );
  }

  // Downloaded from platform → PlayerPage (download detail view)
  if (sourceType === 'web' && mediaId) {
    return <PlayerPage resourceId={resourceId} mediaId={mediaId} />;
  }

  // Uploaded/imported → ResourceDetail (resource management view)
  return <ResourceDetail resourceId={resourceId} />;
}
