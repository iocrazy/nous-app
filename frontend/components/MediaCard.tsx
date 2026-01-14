
import React, { useState } from 'react';
import { DouyinBase, DownloadStatus } from '../types';
import {
  Heart, MessageCircle, Share2, Bookmark, Download, Music, Image as ImageIcon, Video, User, Tag, ChevronLeft, ChevronRight,
  Clock, Timer, Copy, PenTool, FileText, Wand2, Check, Loader2, Play, RefreshCw
} from 'lucide-react';
import { isVideoType, getAwemeTypeLabel, getVideoUrl, getCoverUrl } from '../utils/awemeType';
import { getDownloadUrl } from '../services/dataService';
import { supabase } from '../supabaseClient';

interface MediaCardProps {
  data: DouyinBase;
  onSave?: (data: DouyinBase) => void;
  onUpdate?: (id: string, updates: Partial<DouyinBase>) => void;
}

// Helper to generate consistent colors from strings (Shared logic)
const getTagStyle = (tag: string) => {
  const styles = [
    'bg-rose-500/10 text-rose-400 border-rose-500/20',
    'bg-orange-500/10 text-orange-400 border-orange-500/20',
    'bg-amber-500/10 text-amber-400 border-amber-500/20',
    'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
    'bg-lime-500/10 text-lime-400 border-lime-500/20',
    'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    'bg-teal-500/10 text-teal-400 border-teal-500/20',
    'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
    'bg-sky-500/10 text-sky-400 border-sky-500/20',
    'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
    'bg-violet-500/10 text-violet-400 border-violet-500/20',
    'bg-purple-500/10 text-purple-400 border-purple-500/20',
    'bg-fuchsia-500/10 text-fuchsia-400 border-fuchsia-500/20',
    'bg-pink-500/10 text-pink-400 border-pink-500/20',
  ];
  
  let hash = 0;
  for (let i = 0; i < tag.length; i++) {
    hash = tag.charCodeAt(i) + ((hash << 5) - hash);
  }
  return styles[Math.abs(hash) % styles.length];
};

export const MediaCard: React.FC<MediaCardProps> = ({ data, onSave, onUpdate }) => {
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [copied, setCopied] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [retrySuccess, setRetrySuccess] = useState(false);
  const [showRetryMenu, setShowRetryMenu] = useState(false);

  // AI Feature States - 从数据中加载已有内容
  const [rewrittenText, setRewrittenText] = useState<string | null>(data.ai_rewrite_text || null);
  const [analysisText, setAnalysisText] = useState<string | null>(data.ai_analyze_text || null);
  const [extractText, setExtractText] = useState<string | null>(data.ai_extract_text || null);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const isVideo = isVideoType(data.aweme_type);
  const images = data.image_download_urls || [];
  const isAlbum = !isVideo && images.length > 1;

  // 视频 URL: 优先使用 download_path
  const videoUrl = getVideoUrl(data);
  // 封面 URL
  const coverUrl = getCoverUrl(data);

  const handleSlide = (direction: 'left' | 'right') => {
    if (direction === 'left') {
        setCurrentImageIndex(prev => (prev === 0 ? images.length - 1 : prev - 1));
    } else {
        setCurrentImageIndex(prev => (prev === images.length - 1 ? 0 : prev + 1));
    }
  };
  
  // Format numbers (e.g., 12500 -> 12.5k)
  const formatNumber = (num?: number) => {
    if (!num) return '0';
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toString();
  };

  const formatDateTime = (isoString?: string) => {
    if (!isoString) return 'N/A';
    try {
      const date = new Date(isoString);
      const pad = (n: number) => n.toString().padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    } catch (e) { return isoString; }
  };

  const formatDuration = (secondsStr?: string) => {
     if (!secondsStr) return '0s';
     return `${secondsStr}s`;
  };

  const handleDownload = async (url: string, filename: string) => {
    setIsDownloading(true);
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error('Network response was not ok');
        const blob = await response.blob();
        const blobUrl = window.URL.createObjectURL(blob);
        
        const link = document.createElement('a');
        link.href = blobUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(blobUrl);
    } catch (error) {
        console.warn('Direct download failed, falling back to new tab:', error);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    } finally {
        setIsDownloading(false);
    }
  };

  const onDownloadVideo = async () => {
    if (!data.aweme_id) return;
    setIsDownloading(true);

    try {
      // 获取认证 token
      const { data: sessionData } = await supabase?.auth.getSession() || {};
      const token = sessionData?.session?.access_token;

      if (!token) {
        console.error('No auth token available');
        // 回退到静态文件下载
        if (videoUrl) {
          handleDownload(videoUrl, `${data.aweme_id}.mp4`);
        }
        return;
      }

      // 使用 API 下载端点（带认证）
      const downloadUrl = getDownloadUrl(data.aweme_id);
      const response = await fetch(downloadUrl, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      if (!response.ok) {
        throw new Error(`Download failed: ${response.status}`);
      }

      // 从 Content-Disposition 获取文件名
      const contentDisposition = response.headers.get('Content-Disposition');
      let filename = `${data.aweme_id}.mp4`;
      if (contentDisposition) {
        const match = contentDisposition.match(/filename="(.+)"/);
        if (match) {
          filename = match[1];
        }
      }

      // 创建 blob 并下载
      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(blobUrl);

    } catch (error) {
      console.error('Download error:', error);
      // 回退到静态文件播放（新标签页打开）
      if (videoUrl) {
        window.open(videoUrl, '_blank');
      }
    } finally {
      setIsDownloading(false);
    }
  };

  const onDownloadImages = () => {
    if (!data.image_download_urls) return;
    data.image_download_urls.forEach((url, idx) => {
        // Stagger downloads slightly
        setTimeout(() => {
            handleDownload(url, `${data.aweme_id || 'image'}_${idx + 1}.jpg`);
        }, idx * 500);
    });
  };

  const onDownloadAudio = () => {
    const url = data.music_download_urls?.[0];
    if (!url || url === '#') return;
    handleDownload(url, `${data.aweme_id || 'music'}.mp3`);
  };

  // 重新获取/下载
  const onRefetch = async (options: { video?: boolean; music?: boolean; cover?: boolean }) => {
    if (!data.aweme_id || !data.video_original_url) return;
    setIsRetrying(true);
    setRetrySuccess(false);
    setShowRetryMenu(false);

    try {
      // 获取认证 token
      const { data: sessionData } = await supabase?.auth.getSession() || {};
      const token = sessionData?.session?.access_token;

      if (!token) {
        console.error('No auth token available');
        return;
      }

      // 调用后端 fetch API 重新获取
      const apiUrl = import.meta.env?.VITE_API_URL || 'http://localhost:8080';
      const response = await fetch(`${apiUrl}/api/v1/douyin/fetch`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          url: data.video_original_url,
          video_bool: options.video ?? false,
          music_bool: options.music ?? false,
          cover_bool: options.cover ?? false,
          video_categories: data.video_categories || ''
        })
      });

      if (!response.ok) {
        throw new Error(`Refetch failed: ${response.status}`);
      }

      setRetrySuccess(true);
      // 3秒后重置成功状态
      setTimeout(() => setRetrySuccess(false), 3000);

    } catch (error) {
      console.error('Refetch error:', error);
    } finally {
      setIsRetrying(false);
    }
  };

  // 保存 AI 内容到数据库
  const saveAIContent = (field: string, content: string) => {
    if (onUpdate && data.aweme_id) {
      const updates: Partial<DouyinBase> = {
        [field]: content,
        ai_generated_at: new Date().toISOString()
      };
      onUpdate(data.aweme_id, updates);
    }
  };

  const handleAction = (e: React.MouseEvent, action: string) => {
    e.stopPropagation();
    if (action === 'copy') {
      const textToCopy = data.video_desc || data.video_title || "";
      if (textToCopy) {
        navigator.clipboard.writeText(textToCopy).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        });
      }
    } else if (action === 'rewrite') {
        setLoadingAction('rewrite');
        // Mock API latency for demo - TODO: 接入真实 AI API
        setTimeout(() => {
            const generatedText = "✨ Here's a catchy version: Check out this mind-blowing #coding feature we just shipped! 🚀 React & Gemini are changing the game. Don't miss this! 🔥";
            setRewrittenText(generatedText);
            saveAIContent('ai_rewrite_text', generatedText);
            setLoadingAction(null);
        }, 1500);
    } else if (action === 'extract') {
        setLoadingAction('extract');
        setTimeout(() => {
            const generatedText = "📝 Extracted Summary:\n- Topic: React & Gemini Integration\n- Key Feature: UI Generation\n- Target Audience: Developers\n- Tone: Exciting, Tech-focused";
            setExtractText(generatedText);
            saveAIContent('ai_extract_text', generatedText);
            setLoadingAction(null);
        }, 1500);
    } else if (action === 'analyze') {
        setLoadingAction('analyze');
        setTimeout(() => {
            const generatedText = "📊 Analysis: This content targets tech enthusiasts. Key engagement drivers are the 'React' and 'Gemini' keywords. The visual hook appears at 0:05. Sentiment is highly positive.";
            setAnalysisText(generatedText);
            saveAIContent('ai_analyze_text', generatedText);
            setLoadingAction(null);
        }, 1500);
    } else {
      console.log(`Action triggered: ${action}`);
    }
  };

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden hover:border-zinc-700 transition-all duration-300 shadow-lg flex flex-col">
      <div className="flex flex-col md:flex-row">
        {/* Media Preview Section - Left Side */}
        <div className="md:w-2/5 bg-black relative h-64 md:h-auto md:max-h-[70vh] md:min-h-[400px] group flex-shrink-0 flex items-center justify-center">
          {isVideo ? (
            <div
              className="w-full h-full flex items-center justify-center bg-zinc-900 text-zinc-500 cursor-pointer relative overflow-hidden"
              onClick={() => setIsPlaying(true)}
            >
               {isPlaying ? (
                 <video
                   src={videoUrl}
                   className="w-full h-full object-contain bg-black"
                   controls
                   autoPlay
                   playsInline
                 />
               ) : (
                 <>
                   <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent z-10 pointer-events-none" />
                   <img
                    src={coverUrl || "https://picsum.photos/400/600"}
                    alt="Thumbnail"
                    className="w-full h-full object-contain opacity-90"
                   />
                   <div className="absolute z-20 w-16 h-16 bg-white/10 backdrop-blur-md rounded-full flex items-center justify-center group-hover:scale-110 transition-transform border border-white/20 shadow-xl">
                      <Play className="w-8 h-8 text-white fill-white ml-1" />
                   </div>
                 </>
               )}
            </div>
          ) : (
            <div className="w-full h-full relative">
               <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent z-10" />
               <img 
                src={(isAlbum ? images[currentImageIndex] : images[0]) || "https://picsum.photos/400/600"} 
                alt="Cover" 
                className="w-full h-full object-cover transition-opacity duration-300"
               />
               
               {isAlbum ? (
                 <>
                    {/* Carousel Controls */}
                    <button 
                      onClick={() => handleSlide('left')}
                      className="absolute left-4 top-1/2 -translate-y-1/2 p-2 bg-black/50 hover:bg-black/70 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity z-30"
                    >
                      <ChevronLeft size={20} />
                    </button>
                    <button 
                      onClick={() => handleSlide('right')}
                      className="absolute right-4 top-1/2 -translate-y-1/2 p-2 bg-black/50 hover:bg-black/70 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity z-30"
                    >
                      <ChevronRight size={20} />
                    </button>
                    {/* Index Indicator */}
                    <div className="absolute top-4 left-4 z-20 bg-black/60 px-3 py-1 rounded-full text-xs text-white font-medium backdrop-blur-md">
                      {currentImageIndex + 1} / {images.length}
                    </div>
                 </>
               ) : (
                 <ImageIcon className="absolute top-4 right-4 z-20 w-6 h-6 text-white drop-shadow-md" />
               )}
            </div>
          )}
          
          {/* Hide author overlay when playing to prevent obstruction of controls */}
          {!isPlaying && (
            <div className="absolute bottom-4 left-4 z-20 flex items-center space-x-2 text-white pointer-events-none">
                <User className="w-4 h-4" />
                <span className="font-medium text-sm drop-shadow-md">@{data.author || 'Unknown'}</span>
            </div>
          )}
        </div>

        {/* Info Section - Right Side */}
        <div className="flex-1 p-6 flex flex-col md:max-h-[70vh] overflow-y-auto custom-scrollbar">
          
          {/* Header Metadata */}
          <div className="flex justify-between items-start mb-2">
            <div className="flex gap-2">
                <span className="px-2 py-1 text-xs font-semibold bg-zinc-800 text-zinc-300 rounded-md border border-zinc-700 uppercase tracking-wider">
                {getAwemeTypeLabel(data.aweme_type)}
                </span>
                {data.video_resolution && (
                <span className="px-2 py-1 text-xs font-semibold bg-indigo-900/30 text-indigo-400 rounded-md border border-indigo-900/50">
                {data.video_resolution}
                </span>
                )}
            </div>
            <span className="text-xs text-zinc-500 font-mono">ID: {data.aweme_id}</span>
          </div>

          {/* Title */}
          <h2 className="text-2xl font-bold text-zinc-100 mb-3 leading-tight">
            {data.video_title || 'No Title'}
          </h2>

          {/* Time & Duration Row */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-y-2 gap-x-6 mb-5 text-sm text-zinc-400">
             <div className="flex items-center gap-2">
                <Clock size={14} className="text-zinc-500"/>
                <span>Video Release Time: <span className="text-zinc-300 font-medium">{formatDateTime(data.video_created_time)}</span></span>
             </div>
             <div className="flex items-center gap-2">
                <Timer size={14} className="text-zinc-500"/>
                <span>Video Duration: <span className="text-zinc-300 font-medium">{formatDuration(data.video_duration)}</span></span>
             </div>
          </div>

          {/* Stats Grid */}
          <div className="grid grid-cols-4 gap-4 mb-6">
            <div className="flex flex-col items-center justify-center p-3 bg-zinc-950 rounded-xl border border-zinc-800">
              <Heart className="w-5 h-5 text-rose-500 mb-1" />
              <span className="text-sm font-bold text-white">{formatNumber(data.video_digg_count)}</span>
              <span className="text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Likes</span>
            </div>
            <div className="flex flex-col items-center justify-center p-3 bg-zinc-950 rounded-xl border border-zinc-800">
              <MessageCircle className="w-5 h-5 text-sky-500 mb-1" />
              <span className="text-sm font-bold text-white">{formatNumber(data.video_comment_count)}</span>
              <span className="text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Comments</span>
            </div>
            <div className="flex flex-col items-center justify-center p-3 bg-zinc-950 rounded-xl border border-zinc-800">
              <Share2 className="w-5 h-5 text-emerald-500 mb-1" />
              <span className="text-sm font-bold text-white">{formatNumber(data.video_share_count)}</span>
              <span className="text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Shares</span>
            </div>
            <div className="flex flex-col items-center justify-center p-3 bg-zinc-950 rounded-xl border border-zinc-800">
              <Bookmark className="w-5 h-5 text-amber-500 mb-1" />
              <span className="text-sm font-bold text-white">{formatNumber(data.video_collect_count)}</span>
              <span className="text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Collects</span>
            </div>
          </div>

          {/* Tags */}
          {data.tags && data.tags.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-4">
              {data.tags.map((tag, i) => (
                <span key={i} className={`px-2.5 py-1 rounded-full border flex items-center gap-1.5 text-xs font-medium ${getTagStyle(tag)}`}>
                  <Tag size={10} className="opacity-70" />
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Action Buttons Row */}
          <div className="grid grid-cols-4 gap-2 mb-5">
             <button 
               onClick={(e) => handleAction(e, 'copy')}
               className="flex items-center justify-center gap-2 p-2.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors border border-zinc-700 hover:border-zinc-600"
             >
               {copied ? <Check size={16} className="text-green-500" /> : <Copy size={16} />}
               <span className="text-xs font-medium">{copied ? 'Copied' : 'Copy'}</span>
             </button>
             
             {/* Extract Button - Moved before Rewrite */}
             <button 
               onClick={(e) => handleAction(e, 'extract')}
               disabled={loadingAction === 'extract'}
               className="flex items-center justify-center gap-2 p-2.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors border border-zinc-700 hover:border-zinc-600 disabled:opacity-50"
             >
               {loadingAction === 'extract' ? <Loader2 size={16} className="animate-spin" /> : <FileText size={16} />}
               <span className="text-xs font-medium">Extract</span>
             </button>
             
             {/* Rewrite Button */}
             <button 
               onClick={(e) => handleAction(e, 'rewrite')}
               disabled={loadingAction === 'rewrite'}
               className="flex items-center justify-center gap-2 p-2.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors border border-zinc-700 hover:border-zinc-600 disabled:opacity-50"
             >
               {loadingAction === 'rewrite' ? <Loader2 size={16} className="animate-spin" /> : <PenTool size={16} />}
               <span className="text-xs font-medium">Rewrite</span>
             </button>
             
             {/* Analyze Button */}
             <button 
               onClick={(e) => handleAction(e, 'analyze')}
               disabled={loadingAction === 'analyze'}
               className="flex items-center justify-center gap-2 p-2.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors border border-zinc-700 hover:border-zinc-600 disabled:opacity-50"
             >
               {loadingAction === 'analyze' ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
               <span className="text-xs font-medium">Analyze</span>
             </button>
          </div>

          {/* Description - No Background - Adaptive */}
          <div className="mb-4">
             <p className="text-zinc-300 text-sm whitespace-pre-wrap leading-relaxed">
               {data.video_desc || <span className="text-zinc-500 italic">No description available.</span>}
             </p>
          </div>

          {/* AI Content Area - Adaptive */}
          <div className="space-y-4 mb-6 flex-grow">
            
            {/* Extract Result - Placed at top of AI results */}
            {extractText && (
                <div className="animate-in fade-in slide-in-from-top-2">
                    <div className="flex items-center gap-2 mb-2">
                        <div className="p-1 rounded bg-teal-500/10 text-teal-400">
                           <FileText size={12} />
                        </div>
                        <span className="text-xs font-semibold text-teal-200">Extracted Data</span>
                    </div>
                    <div className="p-3 rounded-lg border border-teal-500/20 bg-teal-500/5 text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                        {extractText}
                    </div>
                </div>
            )}

            {/* Rewrite Result */}
            {rewrittenText && (
                <div className="animate-in fade-in slide-in-from-top-2">
                    <div className="flex items-center gap-2 mb-2">
                        <div className="p-1 rounded bg-indigo-500/10 text-indigo-400">
                           <PenTool size={12} />
                        </div>
                        <span className="text-xs font-semibold text-indigo-200">AI Rewrite</span>
                    </div>
                    <div className="p-3 rounded-lg border border-indigo-500/20 bg-indigo-500/5 text-sm text-zinc-300 leading-relaxed">
                        {rewrittenText}
                    </div>
                </div>
            )}

            {/* Analysis Result */}
            {analysisText && (
                <div className="animate-in fade-in slide-in-from-top-2">
                    <div className="flex items-center gap-2 mb-2">
                        <div className="p-1 rounded bg-purple-500/10 text-purple-400">
                           <Wand2 size={12} />
                        </div>
                        <span className="text-xs font-semibold text-purple-200">Content Analysis</span>
                    </div>
                    <div className="p-3 rounded-lg border border-purple-500/20 bg-purple-500/5 text-sm text-zinc-300 leading-relaxed">
                        {analysisText}
                    </div>
                </div>
            )}
         </div>

          {/* Downloads Footer */}
          <div className="space-y-3 pt-4 border-t border-zinc-800/50 mt-auto">
             <div className="flex gap-3">
                {isVideo && videoUrl && (
                  <button
                    onClick={onDownloadVideo}
                    disabled={isDownloading}
                    className="flex-1 flex items-center justify-center gap-2 bg-zinc-100 hover:bg-white text-black py-3 rounded-lg font-semibold transition-colors text-sm shadow-lg shadow-white/5 disabled:opacity-70"
                  >
                    {isDownloading ? <Loader2 className="w-4 h-4 animate-spin"/> : <Download className="w-4 h-4" />}
                    {isDownloading ? 'Downloading...' : 'Download Video'}
                  </button>
                )}
                {data.image_download_urls && data.image_download_urls.length > 0 && (
                   <button 
                     onClick={onDownloadImages}
                     className="flex-1 flex items-center justify-center gap-2 bg-zinc-100 hover:bg-white text-black py-3 rounded-lg font-semibold transition-colors text-sm shadow-lg shadow-white/5"
                   >
                   <Download className="w-4 h-4" />
                   Download Images
                 </button>
                )}
                {onSave && (
                   <button
                    onClick={() => onSave(data)}
                    className="px-5 py-3 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg transition-colors border border-zinc-700"
                    title="Save to Library"
                   >
                     <Bookmark className="w-5 h-5" />
                   </button>
                )}
                {/* 重新获取按钮 */}
                <div className="relative">
                  <button
                    onClick={() => setShowRetryMenu(!showRetryMenu)}
                    disabled={isRetrying}
                    className={`px-5 py-3 rounded-lg transition-colors border ${
                      retrySuccess
                        ? 'bg-emerald-600/20 text-emerald-400 border-emerald-600/50'
                        : 'bg-zinc-800 text-zinc-300 border-zinc-700 hover:bg-zinc-700 hover:text-white'
                    } disabled:opacity-70`}
                    title={retrySuccess ? 'Request submitted!' : 'Refetch Media'}
                  >
                    {isRetrying ? (
                      <Loader2 className="w-5 h-5 animate-spin" />
                    ) : retrySuccess ? (
                      <Check className="w-5 h-5" />
                    ) : (
                      <RefreshCw className="w-5 h-5" />
                    )}
                  </button>

                  {/* 下拉菜单 */}
                  {showRetryMenu && (
                    <>
                      {/* 点击外部关闭 */}
                      <div
                        className="fixed inset-0 z-40"
                        onClick={() => setShowRetryMenu(false)}
                      />
                      <div className="absolute bottom-full right-0 mb-2 w-48 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl z-50 overflow-hidden">
                        <div className="py-1">
                          <button
                            onClick={() => onRefetch({ video: true })}
                            className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-3 transition-colors"
                          >
                            <Video size={16} className="text-indigo-400" />
                            Download Video
                          </button>
                          <button
                            onClick={() => onRefetch({ cover: true })}
                            className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-3 transition-colors"
                          >
                            <ImageIcon size={16} className="text-emerald-400" />
                            Download Cover
                          </button>
                          <button
                            onClick={() => onRefetch({ music: true })}
                            className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-3 transition-colors"
                          >
                            <Music size={16} className="text-amber-400" />
                            Download Audio
                          </button>
                          <div className="border-t border-zinc-700 my-1" />
                          <button
                            onClick={() => onRefetch({ video: true, cover: true, music: true })}
                            className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-3 transition-colors"
                          >
                            <Download size={16} className="text-sky-400" />
                            Download All
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
             </div>
             
             {data.need_download_music && (
               <button 
                 onClick={onDownloadAudio}
                 className="w-full flex items-center justify-between px-4 py-3 bg-zinc-950 rounded-lg border border-zinc-800 text-xs text-zinc-400 hover:text-zinc-200 hover:border-zinc-700 transition-colors"
               >
                 <div className="flex items-center gap-3">
                   <div className="p-1.5 bg-indigo-500/10 rounded-md">
                      <Music className="w-3.5 h-3.5 text-indigo-500" />
                   </div>
                   <span className="truncate max-w-[250px] font-medium">{data.music_name || 'Original Audio'}</span>
                 </div>
                 <Download className="w-3.5 h-3.5" />
               </button>
             )}
          </div>
        </div>
      </div>
    </div>
  );
};
