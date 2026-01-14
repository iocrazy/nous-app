
import React, { useState, useMemo } from 'react';
import { DouyinBase } from '../types';
import {
  Video, Image as ImageIcon, Music, Tag, Edit2, Check, X, ExternalLink,
  Heart, MessageCircle, Share2, ArrowUpDown, ArrowUp, ArrowDown, Clock, Copy, Play
} from 'lucide-react';
import { isVideoType, getAwemeTypeLabel, getVideoUrl, getCoverUrl } from '../utils/awemeType';

interface LibraryTableProps {
  data: DouyinBase[];
  onUpdate: (id: string, updates: Partial<DouyinBase>) => void;
}

type SortKey = keyof DouyinBase | 'video_created_time';

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

export const LibraryTable: React.FC<LibraryTableProps> = ({ data, onUpdate }) => {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<{ notes: string; tags: string }>({ notes: '', tags: '' });
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [activeMedia, setActiveMedia] = useState<{type: 'video' | 'image', url: string} | null>(null);
  
  // Sorting State
  const [sortConfig, setSortConfig] = useState<{ key: SortKey; direction: 'asc' | 'desc' } | null>(null);

  const startEditing = (item: DouyinBase) => {
    setEditingId(item.aweme_id);
    // 优先使用 tags 数组，如果为空则从 video_categories 解析
    const tagsValue = item.tags && item.tags.length > 0
      ? item.tags.join(', ')
      : item.video_categories || '';
    setEditForm({
      notes: item.notes || '',
      tags: tagsValue
    });
  };

  const cancelEditing = () => {
    setEditingId(null);
    setEditForm({ notes: '', tags: '' });
  };

  const saveEditing = (id: string) => {
    const tagsArray = editForm.tags.split(',').map(t => t.trim()).filter(t => t.length > 0);
    // 同时更新 tags（前端数组）和 video_categories（数据库字段，逗号分隔字符串）
    onUpdate(id, {
      notes: editForm.notes,
      tags: tagsArray,
      video_categories: tagsArray.join(', ')
    });
    setEditingId(null);
  };

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

  const handleMediaClick = (item: DouyinBase) => {
    if (isVideoType(item.aweme_type)) {
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
        let aVal = a[sortConfig.key];
        let bVal = b[sortConfig.key];

        // Handle undefined values
        if (aVal === undefined && bVal === undefined) return 0;
        if (aVal === undefined) return 1;
        if (bVal === undefined) return -1;

        // Date comparison
        if (sortConfig.key === 'video_created_time') {
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

  return (
    <>
      <div className="w-full overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/50">
        <table className="w-full text-left text-sm text-zinc-400">
          <thead className="bg-zinc-900 text-zinc-200 uppercase text-xs font-semibold tracking-wider">
            <tr>
              <th className="px-4 py-4 w-20">Media</th>
              
              {/* Info / Time Sortable */}
              <th 
                className="px-4 py-4 cursor-pointer hover:bg-zinc-800/50 transition-colors w-40"
                onClick={() => handleSort('video_created_time')}
              >
                <div className="flex items-center gap-2">
                  Info <span className="text-zinc-500 text-[10px] lowercase font-normal">(Time)</span>
                  {getSortIcon('video_created_time')}
                </div>
              </th>

              {/* Content Column (New) */}
              <th className="px-4 py-4 w-1/4">Content</th>

              {/* Split Stats Columns */}
              <th 
                className="px-4 py-4 text-center cursor-pointer hover:bg-zinc-800/50 transition-colors w-24"
                onClick={() => handleSort('video_digg_count')}
                title="Sort by Likes"
              >
                <div className="flex items-center justify-center gap-1.5 text-zinc-400 hover:text-rose-400 transition-colors">
                   <Heart size={14} className="text-rose-500" /> 
                   {getSortIcon('video_digg_count')}
                </div>
              </th>
              <th 
                className="px-4 py-4 text-center cursor-pointer hover:bg-zinc-800/50 transition-colors w-24"
                onClick={() => handleSort('video_comment_count')}
                title="Sort by Comments"
              >
                 <div className="flex items-center justify-center gap-1.5 text-zinc-400 hover:text-sky-400 transition-colors">
                   <MessageCircle size={14} className="text-sky-500" /> 
                   {getSortIcon('video_comment_count')}
                 </div>
              </th>
              <th 
                className="px-4 py-4 text-center cursor-pointer hover:bg-zinc-800/50 transition-colors w-24"
                onClick={() => handleSort('video_share_count')}
                title="Sort by Shares"
              >
                 <div className="flex items-center justify-center gap-1.5 text-zinc-400 hover:text-emerald-400 transition-colors">
                   <Share2 size={14} className="text-emerald-500" /> 
                   {getSortIcon('video_share_count')}
                 </div>
              </th>

              <th className="px-4 py-4 w-1/6">Tags</th>
              <th className="px-4 py-4 w-1/6">Notes</th>
              <th className="px-4 py-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {sortedData.map((item) => (
              <tr key={item.aweme_id} className="hover:bg-zinc-900/80 transition-colors">
                <td className="px-4 py-4">
                  <div 
                    onClick={() => handleMediaClick(item)}
                    className="w-16 h-16 bg-zinc-800 rounded-lg overflow-hidden relative flex-shrink-0 group cursor-pointer border border-zinc-700 hover:border-zinc-500 transition-colors"
                  >
                     <img
                      src={getCoverUrl(item) || "https://picsum.photos/400/600"}
                      alt="Preview"
                      className="w-full h-full object-cover opacity-80 group-hover:opacity-60 transition-all"
                     />
                     <div className="absolute inset-0 flex items-center justify-center transition-transform duration-200 group-hover:scale-110">
                        {isVideoType(item.aweme_type) ? (
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
                      <span className="font-medium text-zinc-200 line-clamp-1" title={item.video_title}>{item.video_title || 'Untitled'}</span>
                      
                      <div className="flex flex-col gap-0.5">
                         <span className="text-xs text-zinc-400">@{item.author}</span>
                         <span className="text-[10px] text-zinc-500 flex items-center gap-1.5">
                            <Clock size={10} />
                            {formatDate(item.video_created_time)}
                         </span>
                      </div>

                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[10px] px-1.5 py-0.5 bg-zinc-800 border border-zinc-700 rounded text-zinc-400 uppercase">{getAwemeTypeLabel(item.aweme_type)}</span>
                        <a href={item.video_original_url} target="_blank" rel="noreferrer" className="text-zinc-600 hover:text-indigo-400">
                          <ExternalLink size={12} />
                        </a>
                      </div>
                   </div>
                </td>

                {/* Content Column with Copy Interaction */}
                <td className="px-4 py-4" onClick={() => handleCopy(item.aweme_id, item.video_desc)}>
                    <div className="group/text cursor-pointer hover:bg-zinc-800/80 p-2 rounded-lg -ml-2 transition-colors relative h-full">
                      {copiedId === item.aweme_id ? (
                        <div className="flex items-center gap-2 text-green-400 text-xs animate-in fade-in duration-200 h-8">
                          <Check size={14} />
                          <span className="font-medium">Copied!</span>
                        </div>
                      ) : (
                        <>
                          <p className="text-xs text-zinc-400 group-hover/text:text-zinc-200 line-clamp-2 leading-relaxed" title="Click to copy">
                            {item.video_desc || <span className="italic opacity-40">No description</span>}
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
                   <span className="font-mono text-zinc-300 text-xs">{formatNumber(item.video_digg_count)}</span>
                </td>
                <td className="px-4 py-4 text-center">
                   <span className="font-mono text-zinc-300 text-xs">{formatNumber(item.video_comment_count)}</span>
                </td>
                <td className="px-4 py-4 text-center">
                   <span className="font-mono text-zinc-300 text-xs">{formatNumber(item.video_share_count)}</span>
                </td>

                <td className="px-4 py-4">
                  {editingId === item.aweme_id ? (
                    <input
                      type="text"
                      value={editForm.tags}
                      onChange={(e) => setEditForm({...editForm, tags: e.target.value})}
                      placeholder="e.g. funny, travel"
                      className="w-full bg-zinc-950 border border-zinc-700 rounded px-2 py-1 text-zinc-200 focus:outline-none focus:border-indigo-500 text-xs"
                    />
                  ) : (
                    <div className="flex flex-wrap gap-1.5">
                      {(() => {
                        // 优先使用 tags 数组，如果为空则从 video_categories 解析
                        const displayTags = item.tags && item.tags.length > 0
                          ? item.tags
                          : item.video_categories?.split(',').map(t => t.trim()).filter(Boolean) || [];

                        return displayTags.length > 0
                          ? displayTags.map((tag, i) => (
                              <span key={i} className={`px-2 py-0.5 text-[10px] rounded-full border flex items-center gap-1 ${getTagStyle(tag)}`}>
                                <Tag size={8} className="opacity-50" />
                                {tag}
                              </span>
                            ))
                          : <span className="text-zinc-600 italic text-xs">No tags</span>;
                      })()}
                    </div>
                  )}
                </td>
                <td className="px-4 py-4">
                  {editingId === item.aweme_id ? (
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
                  {editingId === item.aweme_id ? (
                    <div className="flex items-center justify-end gap-2">
                      <button 
                        onClick={() => saveEditing(item.aweme_id)}
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

      {/* Media Preview Modal */}
      {activeMedia && (
        <div 
          className="fixed inset-0 z-[100] bg-black/90 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200"
          onClick={() => setActiveMedia(null)}
        >
           <button 
             className="absolute top-4 right-4 text-white/50 hover:text-white transition-colors"
             onClick={() => setActiveMedia(null)}
           >
             <X size={32} />
           </button>
           
           <div 
             className="relative max-w-5xl w-full max-h-[85vh] rounded-xl overflow-hidden shadow-2xl bg-black flex items-center justify-center"
             onClick={e => e.stopPropagation()}
           >
              {activeMedia.type === 'video' ? (
                <video 
                  src={activeMedia.url} 
                  controls 
                  autoPlay 
                  className="w-full h-full max-h-[85vh] object-contain"
                />
              ) : (
                <img 
                  src={activeMedia.url} 
                  alt="Preview" 
                  className="w-full h-full max-h-[85vh] object-contain"
                />
              )}
           </div>
        </div>
      )}
    </>
  );
};
