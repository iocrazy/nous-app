import { useState, useEffect } from 'react';
import { Video } from '../types';
import { isSupabaseConfigured } from '../supabaseClient';
import { parseShareLink, parseBatchLinks } from '../services/parserService';
import { fetchVideoByPlatformId, saveItem } from '../services/dataService';
import { useDownloadProgress } from './useDownloadProgress';
import { getSystemStatus, SystemStatus } from '../services/systemService';
import { LogEntry } from '../components/TaskMonitor';

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
    audio: false,
    cover: true,
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

  // Download tracking
  const [downloadTaskId, setDownloadTaskId] = useState<string | null>(null);

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

  // Download progress hook
  const {
    status: downloadStatus,
    percent: downloadPercent,
    speed: downloadSpeed,
  } = useDownloadProgress(downloadTaskId, {
    onComplete: async () => {
      addLog('Download completed successfully!', 'success');
      setTaskProgress(100);
      setTaskStatus('Completed');
      loadLibraryData();
      if (currentResult?.platform_id) {
        const updatedVideo = await fetchVideoByPlatformId(currentResult.platform_id);
        if (updatedVideo) {
          setCurrentResult(updatedVideo);
        }
      }
    },
    onError: (error) => {
      addLog(`Download failed: ${error}`, 'error');
    },
  });

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
    setDownloadTaskId(null);

    try {
      addLog('Connecting to backend API...');
      setTaskStatus('Connecting');
      setTaskProgress(10);

      addLog(`Sending request to parse: ${urlInput.substring(0, 35)}...`);
      setTaskStatus('Analyzing');
      setTaskProgress(30);

      const response = await parseShareLink(urlInput, {
        video_bool: downloadOptions.video,
        music_bool: downloadOptions.audio,
        cover_bool: downloadOptions.cover,
      });

      addLog('Backend received the request', 'success');
      setTaskProgress(50);

      if (response.success) {
        addLog(`Video parsed: ${response.title || response.platform_id}`, 'success');
        if ((response as any).fallback_used) {
          addLog(`LightHTTP failed, used fallback: ${(response as any).parse_method_name}`, 'warning');
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

        if (response.download_task_id) {
          addLog('Download task submitted to background queue', 'info');
          addLog(`Tracking download progress: ${response.download_task_id}`, 'info');
          setTaskStatus('Downloading');
          setTaskProgress(60);
          setDownloadTaskId(response.download_task_id);
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
        music_bool: downloadOptions.audio,
        cover_bool: downloadOptions.cover,
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
    downloadTaskId, downloadStatus, downloadPercent, downloadSpeed,

    // Handlers
    handleParse, handleSaveToLibrary, handleBatchSave,
  };
}
