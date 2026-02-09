
import React, { useState, useMemo, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import Hls from 'hls.js';
import { Video } from '../types';
import {
  Video as VideoIcon, Image as ImageIcon, Music, Tag, Edit2, Check, X, ExternalLink,
  Heart, MessageCircle, Share2, ArrowUpDown, ArrowUp, ArrowDown, Clock, Copy, Play, Plus,
  FileText, Sparkles, Eye
} from 'lucide-react';
import { isVideoType, getAwemeTypeLabel, getVideoUrl, getCoverUrl } from '../utils/awemeType';
import { TagSelector } from './TagSelector';

interface LibraryTableProps {
  data: Video[];
  onUpdate: (id: string, updates: Partial<Video>) => void;
  onItemClick?: (item: Video) => void;
}

type SortKey = keyof Video | 'published_at' | 'created_at';

// Helper to generate consistent colors from strings
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

// Helper to get AI status icon styling
const getAIStatusClass = (status?: string): string => {
  switch (status) {
    case 'processing':
      return 'animate-spin text-indigo-400';
    case 'completed':
      return 'text-emerald-400';
    case 'failed':
      return 'text-red-400';
    default:
      return 'text-zinc-600';
  }
};

export const LibraryTable: React.FC<LibraryTableProps> = ({ data, onUpdate, onItemClick }) => {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<{ notes: string }>({ notes: '' });
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [copiedShareId, setCopiedShareId] = useState<string | null>(null);
  const [activeMedia, setActiveMedia] = useState<{type: 'video' | 'image', url: string} | null>(null);

  // HLS video refs
  const modalVideoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);

  // Tag selector state
  const [tagSelectorVideoId, setTagSelectorVideoId] = useState<number | null>(null);
  const [tagSelectorPosition, setTagSelectorPosition] = useState<{ top: number; left: number } | null>(null);
  const tagSelectorRef = useRef<HTMLDivElement>(null);

  // Sorting State - 默认按添加时间降序（最新在前）
  const [sortConfig, setSortConfig] = useState<{ key: SortKey; direction: 'asc' | 'desc' }>({ key: 'created_at', direction: 'desc' });

  // ESC 键关闭全屏预览和标签选择器
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (activeMedia) {
          setActiveMedia(null);
        }
        if (tagSelectorVideoId) {
          setTagSelectorVideoId(null);
          setTagSelectorPosition(null);
        }
      }
    };

    if (activeMedia || tagSelectorVideoId) {
      document.addEventListener('keydown', handleKeyDown);
    }

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [activeMedia, tagSelectorVideoId]);

  // Setup HLS.js for .m3u8 video playback in modal
  useEffect(() => {
    if (!activeMedia || activeMedia.type !== 'video' || !modalVideoRef.current) return;

    const isHls = activeMedia.url.endsWith('.m3u8');
    if (!isHls) return;

    if (Hls.isSupported()) {
      const hls = new Hls();
      hls.loadSource(activeMedia.url);
      hls.attachMedia(modalVideoRef.current);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        modalVideoRef.current?.play();
      });
      hlsRef.current = hls;
    } else if (modalVideoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS support (Safari)
      modalVideoRef.current.src = activeMedia.url;
      modalVideoRef.current.play();
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [activeMedia]);

  const startEditing = (item: Video) => {
    setEditingId(item.platform_id);
    setEditForm({
      notes: item.notes || ''
    });
  };

  const cancelEditing = () => {
    setEditingId(null);
    setEditForm({ notes: '' });
  };

  const saveEditing = (id: string) => {
    onUpdate(id, {
      notes: editForm.notes
    });
    setEditingId(null);
  };

  // Handle tag cell click to open TagSelector
  const handleTagCellClick = (item: Video, event: React.MouseEvent<HTMLTableCellElement>) => {
    if (!item.id) return;

    const rect = event.currentTarget.getBoundingClientRect();
    setTagSelectorPosition({
      top: rect.bottom + window.scrollY + 8,
      left: rect.left + window.scrollX
    });
    setTagSelectorVideoId(item.id);
  };

  // Close TagSelector when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (tagSelectorRef.current && !tagSelectorRef.current.contains(event.target as Node)) {
        setTagSelectorVideoId(null);
        setTagSelectorPosition(null);
      }
    };

    if (tagSelectorVideoId) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [tagSelectorVideoId]);

  const handleSort = (key: SortKey) => {
    let direction: 'asc' | 'desc' = 'desc'; // Default to descending (highest first)
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'desc') {
      direction = 'asc';
    }
    setSortConfig({ key, direction });
  };

  const handleCopy = (id: string, text?: string) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleShareCopy = (id: string, url?: string) => {
    if (!url) return;
    navigator.clipboard.writeText(url);
    setCopiedShareId(id);
    setTimeout(() => setCopiedShareId(null), 2000);
  };

  const handleMediaClick = (item: Video) => {
    if (isVideoType(item.media_type)) {
       const url = getVideoUrl(item);
       if (url && url !== '#') {
         setActiveMedia({ type: 'video', url });
       } else {
         alert("No video stream available to play.");
       }
    } else {
       const url = item.image_download_urls?.[0];
       if (url) {
         setActiveMedia({ type: 'image', url });
       }
    }
  };

  const sortedData = useMemo(() => {
    let items = [...data];
    if (sortConfig !== null) {
      items.sort((a, b) => {
        let aVal: any = a[sortConfig.key as keyof Video];
        let bVal: any = b[sortConfig.key as keyof Video];

        // Handle undefined values
        if (aVal === undefined && bVal === undefined) return 0;
        if (aVal === undefined) return 1;
        if (bVal === undefined) return -1;

        // Date comparison for both published_at and created_at
        if (sortConfig.key === 'published_at' || sortConfig.key === 'created_at') {
           aVal = new Date(aVal as string).getTime();
           bVal = new Date(bVal as string).getTime();
        }

        if (aVal < bVal) {
          return sortConfig.direction === 'asc' ? -1 : 1;
        }
        if (aVal > bVal) {
          return sortConfig.direction === 'asc' ? 1 : -1;
        }
        return 0;
      });
    }
    return items;
  }, [data, sortConfig]);

  const formatNumber = (num?: number) => {
    if (!num) return '0';
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toString();
  };

  const formatDate = (dateString?: string) => {
    if (!dateString) return '-';
    try {
      const date = new Date(dateString);
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    } catch {
      return '-';
    }
  };

  const getSortIcon = (key: SortKey) => {
    if (sortConfig?.key !== key) return <ArrowUpDown size={12} className="text-zinc-600 opacity-50" />;
    return sortConfig.direction === 'asc' 
      ? <ArrowUp size={12} className="text-indigo-400" /> 
      : <ArrowDown size={12} className="text-indigo-400" />;
  };

  if (data.length === 0) {
    return (
       <div className="text-center py-20 bg-zinc-900/30 rounded-2xl border border-dashed border-zinc-800 text-zinc-500">
          No items found matching your search.
       </div>
    );
  }

  // Mobile Card Component
  const MobileCard = ({ item }: { item: Video }) => (
    <div
      className="bg-zinc-900/50 border border-zinc-800 rounded-xl p-3 space-y-3 cursor-pointer active:scale-[0.98] transition-all hover:border-zinc-600"
      onClick={() => onItemClick?.(item)}
    >
      <div className="flex gap-3">
        {/* Thumbnail */}
        <div
          onClick={(e) => { e.stopPropagation(); handleMediaClick(item); }}
          className="w-20 h-20 bg-zinc-800 rounded-lg overflow-hidden relative flex-shrink-0 group cursor-pointer border border-zinc-700"
        >
          <img
            src={getCoverUrl(item) || "https://picsum.photos/400/600"}
            alt="Preview"
            className="w-full h-full object-cover"
            onError={(e) => {
              const target = e.target as HTMLImageElement;
              target.style.display = 'none';
              target.parentElement?.classList.add('bg-zinc-700');
            }}
          />
          <div className="absolute inset-0 flex items-center justify-center">
            {isVideoType(item.media_type) ? (
              <div className="bg-black/40 p-1.5 rounded-full">
                <Play size={16} className="text-white fill-white" />
              </div>
            ) : (
              <ImageIcon size={16} className="text-white drop-shadow-md" />
            )}
          </div>
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0 space-y-1">
          <h3 className="font-medium text-zinc-200 text-sm line-clamp-2 leading-tight">
            {item.title || 'Untitled'}
          </h3>
          <div className="flex items-center gap-2 text-xs text-zinc-500">
            <span>@{item.author}</span>
            <span>•</span>
            <span>{formatDate(item.published_at)}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] px-1.5 py-0.5 bg-zinc-800 border border-zinc-700 rounded text-zinc-400 uppercase">
              {getAwemeTypeLabel(item.media_type)}
            </span>
            <a href={item.original_url} target="_blank" rel="noreferrer" className="text-zinc-600 hover:text-indigo-400">
              <ExternalLink size={12} />
            </a>
          </div>
        </div>
      </div>

      {/* Stats Row */}
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1 text-zinc-400">
            <Heart size={12} className="text-rose-500" />
            {formatNumber(item.like_count)}
          </span>
          <span className="flex items-center gap-1 text-zinc-400">
            <MessageCircle size={12} className="text-sky-500" />
            {formatNumber(item.comment_count)}
          </span>
          <span className="flex items-center gap-1 text-zinc-400">
            <Share2 size={12} className="text-emerald-500" />
            {formatNumber(item.share_count)}
          </span>
        </div>
        <span className="text-[10px] text-zinc-600">
          {formatDate(item.created_at)}
        </span>
      </div>

      {/* AI Status */}
      <div className="flex items-center gap-2 px-1">
        <div className="flex items-center gap-1.5" title={`Transcript: ${item.transcript_status || 'pending'}`}>
          <FileText size={12} className={getAIStatusClass(item.transcript_status)} />
        </div>
        <div className="flex items-center gap-1.5" title={`Summary: ${item.summary_status || 'pending'}`}>
          <Sparkles size={12} className={getAIStatusClass(item.summary_status)} />
        </div>
        <div className="flex items-center gap-1.5" title={`Visual Analysis: ${item.visual_analysis_status || 'pending'}`}>
          <Eye size={12} className={getAIStatusClass(item.visual_analysis_status)} />
        </div>
      </div>

      {/* Tags */}
      {item.tags && item.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {item.tags.slice(0, 4).map((tag, i) => (
            <span key={i} className={`px-2 py-0.5 text-[10px] rounded-full border ${getTagStyle(tag)}`}>
              {tag}
            </span>
          ))}
          {item.tags.length > 4 && (
            <span className="text-[10px] text-zinc-500">+{item.tags.length - 4}</span>
          )}
        </div>
      )}
    </div>
  );

  return (
    <>
      {/* Mobile View - Card List */}
      <div className="md:hidden space-y-3">
        {sortedData.map((item) => (
          <MobileCard key={item.platform_id} item={item} />
        ))}
      </div>

      {/* Desktop View - Table */}
      <div className="hidden md:block w-full overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/50">
        <table className="w-full text-left text-sm text-zinc-400">
          <thead className="bg-zinc-900 text-zinc-200 uppercase text-xs font-semibold tracking-wider">
            <tr>
              <th className="px-4 py-4 w-20">Media</th>
              
              {/* Info Column */}
              <th className="px-4 py-4 w-36">Info</th>

              {/* Source Column */}
              <th className="px-4 py-4 w-20">Source</th>

              {/* Date Added Sortable */}
              <th
                className="px-4 py-4 cursor-pointer hover:bg-zinc-800/50 transition-colors w-28"
                onClick={() => handleSort('created_at')}
                title="Sort by date added"
              >
                <div className="flex items-center gap-2">
                  Date Added
                  {getSortIcon('created_at')}
                </div>
              </th>

              {/* AI Status Column */}
              <th className="px-4 py-4 w-20">AI</th>

              {/* Content Column */}
              <th className="px-4 py-4 w-1/4">Content</th>

              {/* Split Stats Columns */}
              <th 
                className="px-4 py-4 text-center cursor-pointer hover:bg-zinc-800/50 transition-colors w-24"
                onClick={() => handleSort('like_count')}
                title="Sort by Likes"
              >
                <div className="flex items-center justify-center gap-1.5 text-zinc-400 hover:text-rose-400 transition-colors">
                   <Heart size={14} className="text-rose-500" /> 
                   {getSortIcon('like_count')}
                </div>
              </th>
              <th 
                className="px-4 py-4 text-center cursor-pointer hover:bg-zinc-800/50 transition-colors w-24"
                onClick={() => handleSort('comment_count')}
                title="Sort by Comments"
              >
                 <div className="flex items-center justify-center gap-1.5 text-zinc-400 hover:text-sky-400 transition-colors">
                   <MessageCircle size={14} className="text-sky-500" /> 
                   {getSortIcon('comment_count')}
                 </div>
              </th>
              <th 
                className="px-4 py-4 text-center cursor-pointer hover:bg-zinc-800/50 transition-colors w-24"
                onClick={() => handleSort('share_count')}
                title="Sort by Shares"
              >
                 <div className="flex items-center justify-center gap-1.5 text-zinc-400 hover:text-emerald-400 transition-colors">
                   <Share2 size={14} className="text-emerald-500" /> 
                   {getSortIcon('share_count')}
                 </div>
              </th>

              <th className="px-4 py-4 w-1/6">Tags</th>
              <th className="px-4 py-4 w-1/6">Notes</th>
              <th className="px-4 py-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {sortedData.map((item) => (
              <tr key={item.platform_id} className="hover:bg-zinc-900/80 transition-colors">
                <td className="px-4 py-4">
                  <div 
                    onClick={() => handleMediaClick(item)}
                    className="w-16 h-16 bg-zinc-800 rounded-lg overflow-hidden relative flex-shrink-0 group cursor-pointer border border-zinc-700 hover:border-zinc-500 transition-colors"
                  >
                     <img
                      src={getCoverUrl(item) || "https://picsum.photos/400/600"}
                      alt="Preview"
                      className="w-full h-full object-cover opacity-80 group-hover:opacity-60 transition-all"
                      onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.style.display = 'none';
                        target.parentElement?.classList.add('bg-zinc-700');
                      }}
                     />
                     <div className="absolute inset-0 flex items-center justify-center transition-transform duration-200 group-hover:scale-110">
                        {isVideoType(item.media_type) ? (
                          <div className="bg-black/30 p-1.5 rounded-full backdrop-blur-sm shadow-md">
                             <Play size={20} className="text-white fill-white" />
                          </div>
                        ) : (
                          <ImageIcon size={20} className="text-white drop-shadow-md opacity-80" />
                        )}
                     </div>
                  </div>
                </td>
                <td className="px-4 py-4">
                   <div className="flex flex-col gap-1 max-w-xs">
                      <span className="font-medium text-zinc-200 line-clamp-1" title={item.title}>{item.title || 'Untitled'}</span>

                      <div className="flex flex-col gap-0.5">
                         <span className="text-xs text-zinc-400">@{item.author}</span>
                         <span className="text-[10px] text-zinc-500 flex items-center gap-1.5">
                            <Clock size={10} />
                            {formatDate(item.published_at)}
                         </span>
                      </div>

                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[10px] px-1.5 py-0.5 bg-zinc-800 border border-zinc-700 rounded text-zinc-400 uppercase">{getAwemeTypeLabel(item.media_type)}</span>
                        <a href={item.original_url} target="_blank" rel="noreferrer" className="text-zinc-600 hover:text-indigo-400">
                          <ExternalLink size={12} />
                        </a>
                      </div>
                   </div>
                </td>

                {/* Source */}
                <td className="px-4 py-4">
                  <span className="text-xs text-zinc-400 capitalize">
                    {item.source_platform || 'douyin'}
                  </span>
                </td>

                {/* Date Added */}
                <td className="px-4 py-4">
                   <span className="text-xs text-zinc-400 whitespace-nowrap">
                     {formatDate(item.created_at)}
                   </span>
                </td>

                {/* AI Status */}
                <td className="px-4 py-4">
                  <div className="flex items-center gap-2">
                    <FileText size={14} className={getAIStatusClass(item.transcript_status)} title={`Transcript: ${item.transcript_status || 'pending'}`} />
                    <Sparkles size={14} className={getAIStatusClass(item.summary_status)} title={`Summary: ${item.summary_status || 'pending'}`} />
                    <Eye size={14} className={getAIStatusClass(item.visual_analysis_status)} title={`Visual Analysis: ${item.visual_analysis_status || 'pending'}`} />
                  </div>
                </td>

                {/* Content Column with Copy Interaction */}
                <td className="px-4 py-4" onClick={() => handleCopy(item.platform_id, item.description)}>
                    <div className="group/text cursor-pointer hover:bg-zinc-800/80 p-2 rounded-lg -ml-2 transition-colors relative h-full">
                      {copiedId === item.platform_id ? (
                        <div className="flex items-center gap-2 text-green-400 text-xs animate-in fade-in duration-200 h-8">
                          <Check size={14} />
                          <span className="font-medium">Copied!</span>
                        </div>
                      ) : (
                        <>
                          <p className="text-xs text-zinc-400 group-hover/text:text-zinc-200 line-clamp-2 leading-relaxed" title="Click to copy">
                            {item.description || <span className="italic opacity-40">No description</span>}
                          </p>
                          <div className="absolute top-2 right-2 opacity-0 group-hover/text:opacity-100 transition-opacity pointer-events-none bg-zinc-900/80 p-1 rounded backdrop-blur-sm">
                             <Copy size={12} className="text-zinc-400" />
                          </div>
                        </>
                      )}
                    </div>
                </td>
                
                {/* Stats Columns */}
                <td className="px-4 py-4 text-center">
                   <span className="font-mono text-zinc-300 text-xs">{formatNumber(item.like_count)}</span>
                </td>
                <td className="px-4 py-4 text-center">
                   <span className="font-mono text-zinc-300 text-xs">{formatNumber(item.comment_count)}</span>
                </td>
                <td
                  className="px-4 py-4 text-center cursor-pointer hover:bg-emerald-500/5 transition-colors group/share"
                  onClick={() => handleShareCopy(item.platform_id, item.original_url)}
                  title="Click to copy link"
                >
                  {copiedShareId === item.platform_id ? (
                    <div className="flex items-center justify-center gap-1 text-emerald-400">
                      <Check size={12} />
                      <span className="text-xs font-medium">Copied!</span>
                    </div>
                  ) : (
                    <span className="font-mono text-zinc-300 text-xs group-hover/share:text-emerald-400 transition-colors">{formatNumber(item.share_count)}</span>
                  )}
                </td>

                <td
                  className="px-4 py-4 cursor-pointer hover:bg-zinc-800/50 transition-colors group/tags"
                  onClick={(e) => handleTagCellClick(item, e)}
                  title="Click to manage tags"
                >
                  <div className="flex flex-wrap gap-1.5 items-center">
                    {(() => {
                      const displayTags = item.tags || [];

                      return displayTags.length > 0
                        ? (
                          <>
                            {displayTags.slice(0, 3).map((tag, i) => (
                              <span key={i} className={`px-2 py-0.5 text-[10px] rounded-full border flex items-center gap-1 ${getTagStyle(tag)}`}>
                                <Tag size={8} className="opacity-50" />
                                {tag}
                              </span>
                            ))}
                            {displayTags.length > 3 && (
                              <span className="text-[10px] text-zinc-500">+{displayTags.length - 3}</span>
                            )}
                          </>
                        )
                        : (
                          <span className="text-zinc-600 italic text-xs flex items-center gap-1 group-hover/tags:text-zinc-400 transition-colors">
                            <Plus size={10} className="opacity-0 group-hover/tags:opacity-100 transition-opacity" />
                            Add tags
                          </span>
                        );
                    })()}
                  </div>
                </td>
                <td className="px-4 py-4">
                  {editingId === item.platform_id ? (
                    <textarea 
                      value={editForm.notes}
                      onChange={(e) => setEditForm({...editForm, notes: e.target.value})}
                      placeholder="Add notes..."
                      rows={2}
                      className="w-full bg-zinc-950 border border-zinc-700 rounded px-2 py-1 text-zinc-200 focus:outline-none focus:border-indigo-500 resize-none text-xs"
                    />
                  ) : (
                    <p className="text-xs text-zinc-400 line-clamp-2" title={item.notes}>
                      {item.notes || <span className="text-zinc-600 italic">No notes</span>}
                    </p>
                  )}
                </td>
                <td className="px-4 py-4 text-right">
                  {editingId === item.platform_id ? (
                    <div className="flex items-center justify-end gap-2">
                      <button 
                        onClick={() => saveEditing(item.platform_id)}
                        className="p-1.5 bg-indigo-600/20 text-indigo-400 rounded hover:bg-indigo-600/30 transition-colors"
                        title="Save"
                      >
                        <Check size={14} />
                      </button>
                      <button 
                        onClick={cancelEditing}
                        className="p-1.5 bg-red-600/20 text-red-400 rounded hover:bg-red-600/30 transition-colors"
                        title="Cancel"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ) : (
                    <button 
                      onClick={() => startEditing(item)}
                      className="p-2 text-zinc-500 hover:text-indigo-400 hover:bg-zinc-800 rounded transition-colors"
                      title="Edit"
                    >
                      <Edit2 size={14} />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Tag Selector Popup - Using React Portal */}
      {tagSelectorVideoId && tagSelectorPosition && createPortal(
        <div
          ref={tagSelectorRef}
          style={{
            position: 'absolute',
            top: tagSelectorPosition.top,
            left: tagSelectorPosition.left,
            zIndex: 9999,
          }}
          className="animate-in fade-in slide-in-from-top-2 duration-200"
        >
          <div className="w-80 bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl overflow-hidden">
            <TagSelector
              videoId={tagSelectorVideoId}
              inline={true}
              onTagsChange={() => {
                // Tags will be updated via Realtime subscription in App.tsx
              }}
            />
          </div>
        </div>,
        document.body
      )}

      {/* Media Preview Modal - Using React Portal to render outside component hierarchy */}
      {activeMedia && createPortal(
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            width: '100vw',
            height: '100vh',
            zIndex: 99999,
            backgroundColor: 'rgba(0, 0, 0, 0.95)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
          onClick={() => setActiveMedia(null)}
        >
           {/* Media content with close button */}
           <div
             onClick={e => e.stopPropagation()}
             style={{
               position: 'relative',
               borderRadius: '12px',
               overflow: 'visible',
               boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
               backgroundColor: '#000',
             }}
           >
             {/* Close button - positioned at video's top-right */}
             <button
               style={{
                 position: 'absolute',
                 top: '-12px',
                 right: '-12px',
                 zIndex: 10,
                 background: 'rgba(0, 0, 0, 0.8)',
                 border: '2px solid rgba(255, 255, 255, 0.3)',
                 borderRadius: '50%',
                 cursor: 'pointer',
                 padding: '8px',
                 display: 'flex',
                 alignItems: 'center',
                 justifyContent: 'center',
                 transition: 'all 0.2s ease',
               }}
               className="text-white/80 hover:text-white hover:bg-red-600 hover:border-red-500"
               onClick={() => setActiveMedia(null)}
               title="Close (ESC)"
             >
               <X size={24} />
             </button>

             {/* Video/Image content */}
             <div style={{ borderRadius: '12px', overflow: 'hidden' }}>
               {activeMedia.type === 'video' ? (
                 <video
                   ref={modalVideoRef}
                   src={activeMedia.url.endsWith('.m3u8') ? undefined : activeMedia.url}
                   controls
                   autoPlay
                   style={{
                     display: 'block',
                     maxWidth: '90vw',
                     maxHeight: '90vh',
                     objectFit: 'contain',
                   }}
                 />
               ) : (
                 <img
                   src={activeMedia.url}
                   alt="Preview"
                   style={{
                     display: 'block',
                     maxWidth: '90vw',
                     maxHeight: '90vh',
                     objectFit: 'contain',
                   }}
                   onError={(e) => {
                     const target = e.target as HTMLImageElement;
                     target.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 24 24" fill="none" stroke="%23666" stroke-width="1"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>';
                   }}
                 />
               )}
             </div>
           </div>
        </div>,
        document.body
      )}
    </>
  );
};
