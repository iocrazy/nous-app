import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router-dom';
import { Video } from '../../types';
import { fetchVideoByDisplayId, updateItem } from '../../services/dataService';
import { trashResourceByMediaId, trashResourceByPlatformId, updateResource, getVersionHlsUrl } from '../../services/resourceService';
import { fetchMediaByType, extractAudio } from '../../services/parserService';
import { useToast } from '../../components/Toast';
import { useAuth } from '../../contexts/AuthContext';
import { useLibraryContext } from '../../contexts/LibraryContext';
import { getSupabaseClient, isSupabaseConfigured, getSupabaseAccessToken } from '../../supabaseClient';

export const MIN_PANEL_WIDTH = 380;
export const MAX_PANEL_WIDTH = 800;
export const DEFAULT_PANEL_WIDTH = 560;

interface UseDownloadDetailOptions {
  propResourceId?: string;
  propMediaId?: string;
  /** Pre-fetched row from the navigating card. Lets the page paint a
   *  skeleton immediately and survive transient fetch failures. */
  preloaded?: Video;
}

export function useDownloadDetail({ propResourceId, propMediaId, preloaded }: UseDownloadDetailOptions = {}) {
  const { displayId, teamId } = useParams<{ displayId: string; teamId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const from = searchParams.get('from');

  // Support both: direct URL params (legacy /player/:displayId) and props (from ResourceDetailPage)
  const effectiveDisplayId = propMediaId || displayId;

  // Seed video state from the preloaded card data so the page renders
  // immediately. The fetchVideoByDisplayId call below replaces it with
  // the authoritative row when ready; if it fails (network blip,
  // permissions race), we keep the preloaded skeleton visible instead
  // of falling into the "Failed to load" notFound state.
  const [video, setVideo] = useState<Video | null>(preloaded ?? null);
  const [isLoading, setIsLoading] = useState(!preloaded);
  const [notFound, setNotFound] = useState(false);
  const playerRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH);
  const [showDownloadMenu, setShowDownloadMenu] = useState(false);
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [isShareModalOpen, setIsShareModalOpen] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isFetching, setIsFetching] = useState(false);
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
  );
  const { addToast } = useToast();
  const { mediaToken } = useAuth();
  const { setLibrary } = useLibraryContext();
  const isDragging = useRef(false);

  // Track mobile breakpoint for responsive layout
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)');
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  // Resource-level data (from resources table, linked via media_id)
  const [resourceId, setResourceId] = useState<string | null>(null);
  const [resourceRating, setResourceRating] = useState(0);
  const [resourceNotes, setResourceNotes] = useState('');
  const [hlsUrl, setHlsUrl] = useState<string | null>(null);
  const [authToken, setAuthToken] = useState<string | null>(null);

  const handleTimeUpdate = useCallback((seconds: number) => {
    setCurrentTime(seconds);
  }, []);

  const handleDurationChange = useCallback((seconds: number) => {
    setDuration(seconds);
  }, []);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
    dragStartX.current = e.clientX;
    dragStartWidth.current = panelWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handleMouseMove = (ev: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = dragStartX.current - ev.clientX;
      const newWidth = Math.min(MAX_PANEL_WIDTH, Math.max(MIN_PANEL_WIDTH, dragStartWidth.current + delta));
      setPanelWidth(newWidth);
    };

    const handleMouseUp = () => {
      isDragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  }, [panelWidth]);

  useEffect(() => {
    if (!effectiveDisplayId) return;

    // If we already have a preloaded skeleton, keep it visible while
    // we fetch — don't toggle isLoading to true and flash a spinner
    // over content the user is already looking at.
    const hasSkeleton = video !== null;
    if (!hasSkeleton) setIsLoading(true);
    setNotFound(false);

    fetchVideoByDisplayId(effectiveDisplayId).then((data) => {
      if (data) {
        setVideo(data);
      } else if (!hasSkeleton) {
        // Only flip to "not found" when we have nothing to show.
        // With a skeleton present, a transient fetch miss should
        // surface the cached card chrome rather than a hard error.
        setNotFound(true);
      }
      setIsLoading(false);
    });
    // ``video`` intentionally omitted — the effect should run on
    // displayId changes only; ``hasSkeleton`` is captured at call time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveDisplayId]);

  // Realtime: auto-refresh when this parsed_media record is updated (e.g. download completes)
  useEffect(() => {
    if (!video?.id) return;
    const supabase = getSupabaseClient();
    if (!isSupabaseConfigured() || !supabase) return;

    const channel = supabase
      .channel(`player_media_${video.id}`)
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'parsed_media',
          filter: `id=eq.${video.id}`,
        },
        (payload) => {
          setVideo((prev) => prev ? { ...prev, ...payload.new } as Video : prev);
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [video?.id]);

  // Fetch associated resource (rating/notes/HLS) from resources + resource_versions
  useEffect(() => {
    if (!video?.id) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    (async () => {
      // Step 1: Get the resource linked to this parsed_media
      const { data: resource } = await supabase
        .from('resources')
        .select('id, rating, notes, current_version, mime_type')
        .eq('media_id', video.id)
        .limit(1)
        .maybeSingle();

      if (!resource) return;

      const resId = String(resource.id);
      setResourceId(resId);
      setResourceRating(resource.rating || 0);
      setResourceNotes(resource.notes || '');

      // Step 2: Check if the current version has completed HLS transcoding
      // NOTE: Don't gate on resource.mime_type — it's often null.
      // Let the version's hls_path + transcode_status decide.
      const { data: versions } = await supabase
        .from('resource_versions')
        .select('id, hls_path, transcode_status, version_number')
        .eq('resource_id', resource.id)
        .eq('version_number', resource.current_version || 1)
        .limit(1)
        .maybeSingle();

      if (versions?.hls_path && versions.transcode_status === 'completed') {
        const token = await getSupabaseAccessToken();
        setAuthToken(token);
        setHlsUrl(getVersionHlsUrl(resId, String(versions.id), token || undefined));
      }
    })();
  }, [video?.id]);

  const handleRatingChange = useCallback(async (rating: number) => {
    if (!resourceId) return;
    setResourceRating(rating);
    try {
      await updateResource(resourceId, { rating });
    } catch (err) {
      console.error('Failed to update rating:', err);
    }
  }, [resourceId]);

  const handleNotesChange = useCallback((notes: string) => {
    setResourceNotes(notes);
  }, []);

  const handleNotesBlur = useCallback(async () => {
    if (!resourceId) return;
    try {
      await updateResource(resourceId, { notes: resourceNotes || null });
    } catch (err) {
      console.error('Failed to update notes:', err);
    }
  }, [resourceId, resourceNotes]);

  const handleUpdate = async (id: string, updates: Partial<Video>) => {
    const updated = await updateItem(id, updates);
    setVideo((prev) => (prev ? { ...prev, ...updated } : prev));
  };

  const handleDelete = async (_id: string, deleteFiles: boolean) => {
    if (!video) return;
    let trashed = false;
    try {
      // Use platform_id for trash (same method as DownloadsView batch delete)
      if (video.platform_id) {
        if (teamId) {
          await trashResourceByPlatformId(video.platform_id, 'team', teamId);
        } else {
          await trashResourceByPlatformId(video.platform_id);
        }
      } else if (video.id) {
        await trashResourceByMediaId(video.id, teamId ? 'team' : undefined, teamId);
      }
      trashed = true;
    } catch (err) {
      console.error('Trash failed:', err);
      // Try by media_id as fallback (resource might exist with different lookup)
      try {
        if (video.id) {
          await trashResourceByMediaId(video.id, teamId ? 'team' : undefined, teamId);
          trashed = true;
        } else {
          throw new Error('No video id');
        }
      } catch (err2) {
        console.error('Trash by media_id also failed:', err2);
        addToast('Failed to move to trash', 'error');
        return;
      }
    }
    // Remove from library list so user sees it gone when navigating back
    setLibrary(prev => prev.filter(item => item.platform_id !== video.platform_id));
    addToast(trashed ? 'Moved to trash' : 'Deleted', 'success');
    navigate(-1);
  };

  return {
    // Routing
    from,
    teamId,
    isEmbedded: !!propResourceId,
    navigate,
    // Data
    video,
    isLoading,
    notFound,
    playerRef,
    currentTime,
    duration,
    resourceId,
    resourceRating,
    resourceNotes,
    hlsUrl,
    authToken,
    mediaToken,
    // UI state
    panelWidth,
    showDownloadMenu,
    showMoreMenu,
    isShareModalOpen,
    showDeleteDialog,
    isDeleting,
    isDownloading,
    isFetching,
    isMobile,
    // State setters
    setShowDownloadMenu,
    setShowMoreMenu,
    setIsShareModalOpen,
    setShowDeleteDialog,
    setIsDeleting,
    setIsDownloading,
    setIsFetching,
    setVideo,
    // Handlers
    handleTimeUpdate,
    handleDurationChange,
    handleResizeStart,
    handleRatingChange,
    handleNotesChange,
    handleNotesBlur,
    handleUpdate,
    handleDelete,
    handleBack: () => navigate(-1),
    // Download handlers (defined in main component to avoid circular deps)
    addToast,
  };
}
