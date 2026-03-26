import { useState, useEffect, useMemo, useRef } from 'react';
import { Video } from '../types';
import { isSupabaseConfigured } from '../supabaseClient';
import { parseShareLink, parseBatchLinks } from '../services/parserService';
import { fetchVideoByPlatformId, saveItem } from '../services/dataService';
import { getSystemStatus, SystemStatus } from '../services/systemService';
import { LogEntry } from '../components/TaskMonitor';
import { useTaskManager, formatSpeed } from '../contexts/TaskManagerContext';

interface UseParserParams {
  loadLibraryData: () => Promise<void>;
  setLibrary: React.Dispatch<React.SetStateAction<Video[]>>;
  currentResult: Video | null;
  setCurrentResult: React.Dispatch<React.SetStateAction<Video | null>>;
  isAuthenticated: boolean;
}

export function useParser({ loadLibraryData, setLibrary, currentResult, setCurrentResult, isAuthenticated }: UseParserParams) {
  // Input state
  const [urlInput, setUrlInput] = useState('');
  const [parserMode, setParserMode] = useState<'single' | 'batch'>('single');
  const [batchInput, setBatchInput] = useState('');
  const [downloadOptions, setDownloadOptions] = useState({
    video: true,
  });
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);

  // Task state
  const [isParsing, setIsParsing] = useState(false);
  const [taskStatus, setTaskStatus] = useState('Idle');
  const [taskProgress, setTaskProgress] = useState(0);
  const [socketLogs, setSocketLogs] = useState<LogEntry[]>([]);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [batchResults, setBatchResults] = useState<Video[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Download tracking via TaskManagerContext (no more HTTP polling)
  const [downloadCeleryId, setDownloadCeleryId] = useState<string | null>(null);
  // media_id to watch for download task (resolves race condition with Supabase Realtime)
  const [pendingDownloadMediaId, setPendingDownloadMediaId] = useState<string | null>(null);
  // Parse task tracking (async parse flow)
  const [parseUnifiedTaskId, setParseUnifiedTaskId] = useState<string | null>(null);
  const { tasks } = useTaskManager();

  const currentDownloadTask = useMemo(
    () => downloadCeleryId ? tasks.find(t => t.celery_task_id === downloadCeleryId) : undefined,
    [downloadCeleryId, tasks],
  );
  const downloadPercent = currentDownloadTask?.progress ?? 0;
  const downloadSpeed = currentDownloadTask?.speed ? formatSpeed(currentDownloadTask.speed) : undefined;
  const downloadStatus: 'pending' | 'downloading' | 'completed' | 'failed' = (() => {
    if (!currentDownloadTask) return 'pending';
    switch (currentDownloadTask.status) {
      case 'processing': return 'downloading';
      case 'completed': return 'completed';
      case 'failed': return 'failed';
      default: return 'pending';
    }
  })();

  // Sync real-time download progress into taskProgress for TaskMonitor
  // Map backend download 0-100% to TaskMonitor 50-100% (parse = 0-50%, download = 50-100%)
  useEffect(() => {
    if (!downloadCeleryId || !currentDownloadTask) return;
    if (currentDownloadTask.status === 'processing') {
      const mapped = 50 + Math.floor(downloadPercent / 2);
      setTaskProgress(mapped);
      setTaskStatus('Downloading');
    }
  }, [downloadPercent, downloadCeleryId, currentDownloadTask?.status]);

  // Track parse task via unified_task_id
  const currentParseTask = useMemo(
    () => parseUnifiedTaskId ? tasks.find(t => t.id === parseUnifiedTaskId) : undefined,
    [parseUnifiedTaskId, tasks],
  );

  // React to parse completion/failure
  const prevParseStatusRef = useRef<string | null>(null);
  useEffect(() => {
    if (!currentParseTask) return;
    const status = currentParseTask.status;
    if (status === prevParseStatusRef.current) return;
    prevParseStatusRef.current = status;

    if (status === 'completed') {
      addLog('Parse completed! Starting download...', 'success');
      setTaskProgress(50);
      setTaskStatus('Downloading');
      setParseUnifiedTaskId(null);
      setIsParsing(false);

      // Parse complete → download task auto-created by backend
      // Set pendingDownloadMediaId so useEffect below can find download task
      // when it arrives via Supabase Realtime (race condition fix)
      const mediaId = currentParseTask.media_id;
      if (mediaId) {
        setPendingDownloadMediaId(mediaId);
        // Refresh library to show parsed metadata
        fetchVideoByPlatformId(mediaId).then(updated => {
          if (updated) setCurrentResult(updated);
        });
      }
      loadLibraryData();
    } else if (status === 'failed') {
      addLog(`Parse failed: ${currentParseTask.error_msg || 'Unknown error'}`, 'error');
      setTaskStatus('Failed');
      setTaskProgress(0);
      setParseUnifiedTaskId(null);
      setIsParsing(false);
    }
  }, [currentParseTask?.status]);

  // Watch for download task to appear in tasks array (race condition fix)
  // After parse completes, download task may not be in Realtime yet
  useEffect(() => {
    if (!pendingDownloadMediaId || downloadCeleryId) return;
    const dlTask = tasks.find(t =>
      t.task_type === 'download' && t.media_id === pendingDownloadMediaId
    );
    if (dlTask?.celery_task_id) {
      setDownloadCeleryId(dlTask.celery_task_id);
      setPendingDownloadMediaId(null);
      return;
    }
    // Timeout: if no download task discovered within 30s, clear pending state
    const timeout = setTimeout(() => {
      setPendingDownloadMediaId(null);
      addLog('Download task not found within timeout', 'warning');
    }, 30000);
    return () => clearTimeout(timeout);
  }, [tasks, pendingDownloadMediaId, downloadCeleryId]);

  // React to download completion/failure from TaskManager
  const prevDownloadStatusRef = useRef<string | null>(null);
  useEffect(() => {
    if (!currentDownloadTask) return;
    const status = currentDownloadTask.status;
    if (status === prevDownloadStatusRef.current) return;
    prevDownloadStatusRef.current = status;

    if (status === 'completed') {
      addLog('Download completed successfully!', 'success');
      setTaskProgress(100);
      setTaskStatus('Completed');
      loadLibraryData();
      if (currentResult?.platform_id) {
        fetchVideoByPlatformId(currentResult.platform_id).then(updated => {
          if (updated) setCurrentResult(updated);
        });
      }
    } else if (status === 'failed') {
      addLog(`Download failed: ${currentDownloadTask.error_msg || 'Unknown error'}`, 'error');
    }
  }, [currentDownloadTask?.status]);

  // Poll system status
  useEffect(() => {
    if (!isAuthenticated) return;
    const fetchStatus = async () => {
      try {
        const status = await getSystemStatus();
        setSystemStatus(status);
      } catch (err) {
        console.debug('Failed to fetch system status:', err);
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, [isAuthenticated]);

  // Helper to add log
  const addLog = (msg: string, type: 'info' | 'success' | 'warning' | 'error' = 'info') => {
    const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: 'numeric', minute: 'numeric', second: 'numeric' });
    setSocketLogs(prev => [...prev, {
      id: Math.random().toString(36).substr(2, 9),
      time,
      message: msg,
      type,
    }]);
  };

  const handleParse = () => {
    if (parserMode === 'batch') {
      const links = batchInput.split(/\r?\n/).filter(line => line.trim().length > 0);
      if (links.length === 0) return;
      handleBatchParse();
    } else {
      if (!urlInput) return;
      handleSingleParse();
    }
  };

  const handleSingleParse = async () => {
    if (!urlInput) return;
    setIsParsing(true);
    setCurrentResult(null);
    setError(null);
    setSocketLogs([]);
    setTaskProgress(0);
    setTaskStatus('Initializing');
    setDownloadCeleryId(null);
    setPendingDownloadMediaId(null);
    setParseUnifiedTaskId(null);
    prevDownloadStatusRef.current = null;
    prevParseStatusRef.current = null;

    try {
      addLog('Connecting to backend API...');
      setTaskStatus('Connecting');
      setTaskProgress(10);

      addLog(`Sending request to parse: ${urlInput.substring(0, 35)}...`);
      setTaskStatus('Analyzing');
      setTaskProgress(30);

      const response = await parseShareLink(urlInput, {
        video_bool: downloadOptions.video,
      });

      addLog('Backend received the request', 'success');
      setTaskProgress(50);

      if (response.success) {
        if ((response as any).async) {
          // Async mode: parse dispatched to background queue
          addLog('Parse task submitted to background queue', 'info');
          setTaskStatus('Parsing');
          setTaskProgress(30);

          const unifiedTaskId = (response as any).task_id || (response as any).unified_task_id;
          if (unifiedTaskId) {
            setParseUnifiedTaskId(unifiedTaskId);
            addLog(`Tracking parse progress: ${unifiedTaskId}`, 'info');
          }
          // Don't set isParsing=false — wait for parse completion via effect
          return;
        }

        // Synchronous response (legacy / fallback)
        addLog(`Video parsed: ${response.title || response.platform_id}`, 'success');
        if ((response as any).fallback_used) {
          const reason = (response as any).fallback_reason || 'primary parser failed';
          addLog(`${reason}, used fallback: ${(response as any).parse_method_name}`, 'warning');
        } else {
          addLog(`Parse method: ${(response as any).parse_method_name || 'Unknown'}`, 'info');
        }
        addLog(`Author: ${response.author || 'Unknown'}`, 'info');

        const parsedResult: Video = {
          id: response.id,
          platform_id: response.platform_id,
          title: response.title,
          author: response.author,
          media_type: response.media_type,
          original_url: response.original_url || urlInput,
          video_download_urls: response.video_download_urls || [],
          cover_urls: response.cover_urls || [],
          image_download_urls: response.image_download_urls || [],
          like_count: response.like_count || 0,
          comment_count: response.comment_count || 0,
          share_count: response.share_count || 0,
          favorite_count: response.favorite_count || 0,
          duration: response.duration || '0',
          published_at: response.published_at,
          description: response.description,
          resolution: response.resolution,
          video_download_status: (response as any).video_download_status,
        };

        setCurrentResult(parsedResult);

        const dlTaskId = response.task_id || response.download_task_id;
        if (dlTaskId) {
          addLog('Download task submitted to background queue', 'info');
          addLog(`Tracking download progress: ${dlTaskId}`, 'info');
          setTaskStatus('Downloading');
          setTaskProgress(60);
          setDownloadCeleryId(dlTaskId);
        } else {
          setTaskProgress(100);
          setTaskStatus('Completed');
          addLog('Task completed successfully!', 'success');
          await loadLibraryData();
        }
      } else {
        throw new Error(response.message || 'Parse failed');
      }
    } catch (err: any) {
      setError(err.message || 'Failed to parse link');
      addLog(`Error: ${err.message}`, 'error');
      setTaskStatus('Failed');
      setTaskProgress(0);
    } finally {
      setIsParsing(false);
    }
  };

  const handleBatchParse = async () => {
    const links = batchInput.split(/\r?\n/).filter(line => line.trim().length > 0);
    if (links.length === 0) return;

    setIsParsing(true);
    setBatchResults([]);
    setCurrentResult(null);
    setError(null);
    setSocketLogs([]);
    setTaskProgress(0);
    setTaskStatus('Batch Mode Active');

    try {
      addLog(`Starting batch job. Found ${links.length} links to process.`);
      setTaskProgress(10);
      addLog('Sending batch request to backend...');
      setTaskStatus('Processing Batch');

      const response = await parseBatchLinks(links, {
        video_bool: downloadOptions.video,
      });

      setTaskProgress(50);
      addLog(`Backend processed ${response.total} links`, 'info');
      addLog(`Successfully submitted: ${response.submitted}`, 'success');

      if (response.failed > 0) {
        addLog(`Failed: ${response.failed}`, 'warning');
        response.errors.forEach((err) => {
          addLog(`  - ${err.url.substring(0, 30)}...: ${err.error}`, 'error');
        });
      }

      const batchData: Video[] = [];
      response.results.forEach((result: any) => {
        addLog(`  ✓ ${result.platform_id}: ${result.status}`, 'success');
        if (result.data) batchData.push(result.data as Video);
      });

      setTaskProgress(80);
      setBatchResults(batchData);
      loadLibraryData();
      setTaskProgress(100);
      setTaskStatus('Batch Job Completed');
      addLog(`Batch processing finished. ${batchData.length}/${response.total} successful.`, 'success');
    } catch (err: any) {
      setError(err.message || 'Batch processing failed');
      addLog(`Error: ${err.message}`, 'error');
      setTaskStatus('Failed');
    } finally {
      setIsParsing(false);
    }
  };

  const handleSaveToLibrary = async (item: Video, silent = false) => {
    const newItem = { ...item, tags: item.tags || [] };
    try {
      setLibrary(prev => {
        if (prev.find(i => i.platform_id === item.platform_id)) return prev;
        return [newItem, ...prev];
      });
      if (isSupabaseConfigured()) {
        const savedItem = await saveItem(newItem);
        if (!silent) alert('Saved to Cloud Collection!');
      } else {
        if (!silent) alert('Saved to Local Collection (Supabase not configured)');
      }
    } catch (err) {
      console.error('Save failed:', err);
      if (!silent) alert('Failed to save to cloud database.');
    }
  };

  const handleBatchSave = async () => {
    if (batchResults.length === 0) return;
    let savedCount = 0;
    for (const item of batchResults) {
      await handleSaveToLibrary(item, true);
      savedCount++;
    }
    alert(`Successfully saved ${savedCount} items to your library!`);
  };

  return {
    // Input
    urlInput, setUrlInput,
    parserMode, setParserMode,
    batchInput, setBatchInput,
    downloadOptions, setDownloadOptions,
    selectedTagIds, setSelectedTagIds,

    // Task
    isParsing, taskStatus, taskProgress,
    socketLogs, systemStatus,
    batchResults, error,

    // Download
    downloadTaskId: downloadCeleryId, downloadStatus, downloadPercent, downloadSpeed,

    // Handlers
    handleParse, handleSaveToLibrary, handleBatchSave,
  };
}
