/**
 * CleanupSuggestionsView Component - Storage cleanup suggestions page
 */

import React, { useState, useEffect } from 'react';
import {
  Trash2,
  Shield,
  RefreshCw,
  Loader2,
  AlertTriangle,
  HardDrive,
  Eye,
  EyeOff,
  Clock,
  Copy,
  CheckCircle,
  XCircle,
  ChevronDown,
  ChevronRight,
  Filter,
} from 'lucide-react';
import {
  getCleanupData,
  takeCleanupAction,
  batchCleanupAction,
  CleanupSuggestion,
  CleanupStats,
  CleanupReason,
  formatBytes,
  getReasonLabel,
  getReasonColor,
} from '../services/cleanupService';

interface CleanupSuggestionsViewProps {
  onClose?: () => void;
}

// Reason icon mapping
const getReasonIcon = (reason: CleanupReason) => {
  switch (reason) {
    case 'never_viewed':
      return EyeOff;
    case 'old_unused':
      return Clock;
    case 'duplicate_content':
      return Copy;
    case 'large_file':
      return HardDrive;
    default:
      return AlertTriangle;
  }
};

export const CleanupSuggestionsView: React.FC<CleanupSuggestionsViewProps> = ({ onClose }) => {
  const [suggestions, setSuggestions] = useState<CleanupSuggestion[]>([]);
  const [stats, setStats] = useState<CleanupStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [activeFilter, setActiveFilter] = useState<CleanupReason | 'all'>('all');
  const [processingIds, setProcessingIds] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [categories, setCategories] = useState({
    never_viewed: 0,
    old_unused: 0,
    duplicate_content: 0,
    large_file: 0,
  });

  // Load data
  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Use combined API for optimal performance (single network request)
      const data = await getCleanupData(50, true);
      setSuggestions(data.suggestions);
      setCategories(data.categories);
      setStats(data.stats);
    } catch (err) {
      console.error('Failed to load cleanup data:', err);
      setError('Failed to load cleanup suggestions');
    } finally {
      setIsLoading(false);
    }
  };

  // Filter suggestions
  const filteredSuggestions =
    activeFilter === 'all'
      ? suggestions
      : suggestions.filter((s) => s.reason === activeFilter);

  // Selection handlers
  const toggleSelect = (id: number) => {
    const newSelected = new Set(selectedIds);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedIds(newSelected);
  };

  const selectAll = () => {
    setSelectedIds(new Set(filteredSuggestions.map((s) => s.video_id)));
  };

  const deselectAll = () => {
    setSelectedIds(new Set());
  };

  // Action handlers
  const handleAction = async (videoId: number, action: 'delete' | 'keep_forever' | 'dismiss') => {
    setProcessingIds((prev) => new Set(prev).add(videoId));
    try {
      await takeCleanupAction(videoId, action);
      // Remove from suggestions
      setSuggestions((prev) => prev.filter((s) => s.video_id !== videoId));
      setSelectedIds((prev) => {
        const newSet = new Set(prev);
        newSet.delete(videoId);
        return newSet;
      });
    } catch (err) {
      console.error('Action failed:', err);
    } finally {
      setProcessingIds((prev) => {
        const newSet = new Set(prev);
        newSet.delete(videoId);
        return newSet;
      });
    }
  };

  const handleBatchAction = async (action: 'delete' | 'keep_forever' | 'dismiss') => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;

    setProcessingIds(new Set(ids));
    try {
      await batchCleanupAction(ids, action);
      // Remove from suggestions
      setSuggestions((prev) => prev.filter((s) => !selectedIds.has(s.video_id)));
      setSelectedIds(new Set());
    } catch (err) {
      console.error('Batch action failed:', err);
    } finally {
      setProcessingIds(new Set());
    }
  };

  // Calculate totals
  const totalReclaimable = filteredSuggestions.reduce(
    (sum, s) => sum + (s.storage_size || 0),
    0
  );
  const selectedReclaimable = Array.from(selectedIds).reduce((sum, id) => {
    const item = suggestions.find((s) => s.video_id === id);
    return sum + (item?.storage_size || 0);
  }, 0);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 text-indigo-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          icon={HardDrive}
          label="Total Storage"
          value={formatBytes(stats?.total_storage_bytes || 0)}
          color="text-indigo-400"
        />
        <StatCard
          icon={Trash2}
          label="Reclaimable"
          value={formatBytes(stats?.reclaimable_bytes || 0)}
          color="text-red-400"
        />
        <StatCard
          icon={EyeOff}
          label="Never Viewed"
          value={`${stats?.videos_never_viewed || 0} videos`}
          color="text-yellow-400"
        />
        <StatCard
          icon={Shield}
          label="Kept Forever"
          value={`${stats?.videos_marked_keep || 0} videos`}
          color="text-emerald-400"
        />
      </div>

      {/* Filter Tabs */}
      <div className="flex items-center gap-2 flex-wrap">
        <FilterTab
          label="All"
          count={suggestions.length}
          isActive={activeFilter === 'all'}
          onClick={() => setActiveFilter('all')}
        />
        <FilterTab
          label="Never Viewed"
          count={categories.never_viewed}
          isActive={activeFilter === 'never_viewed'}
          onClick={() => setActiveFilter('never_viewed')}
          color="text-yellow-400"
        />
        <FilterTab
          label="Not Recent"
          count={categories.old_unused}
          isActive={activeFilter === 'old_unused'}
          onClick={() => setActiveFilter('old_unused')}
          color="text-orange-400"
        />
        <FilterTab
          label="Duplicates"
          count={categories.duplicate_content}
          isActive={activeFilter === 'duplicate_content'}
          onClick={() => setActiveFilter('duplicate_content')}
          color="text-purple-400"
        />
        <FilterTab
          label="Large Files"
          count={categories.large_file}
          isActive={activeFilter === 'large_file'}
          onClick={() => setActiveFilter('large_file')}
          color="text-red-400"
        />
      </div>

      {/* Batch Actions */}
      {selectedIds.size > 0 && (
        <div className="flex items-center justify-between p-4 bg-zinc-800/50 rounded-xl border border-zinc-700">
          <div className="flex items-center gap-4">
            <span className="text-sm text-zinc-300">
              {selectedIds.size} selected ({formatBytes(selectedReclaimable)})
            </span>
            <button
              onClick={deselectAll}
              className="text-xs text-zinc-500 hover:text-white transition-colors"
            >
              Clear selection
            </button>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => handleBatchAction('keep_forever')}
              className="flex items-center gap-2 px-3 py-1.5 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 rounded-lg text-sm transition-colors"
            >
              <Shield size={14} />
              Keep All
            </button>
            <button
              onClick={() => handleBatchAction('dismiss')}
              className="flex items-center gap-2 px-3 py-1.5 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded-lg text-sm transition-colors"
            >
              <XCircle size={14} />
              Dismiss All
            </button>
            <button
              onClick={() => handleBatchAction('delete')}
              className="flex items-center gap-2 px-3 py-1.5 bg-red-600/20 hover:bg-red-600/30 text-red-400 rounded-lg text-sm transition-colors"
            >
              <Trash2 size={14} />
              Delete All
            </button>
          </div>
        </div>
      )}

      {/* Selection Controls */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={selectAll}
            className="text-sm text-zinc-400 hover:text-white transition-colors"
          >
            Select All ({filteredSuggestions.length})
          </button>
          <span className="text-zinc-600">|</span>
          <span className="text-sm text-zinc-500">
            Potential savings: {formatBytes(totalReclaimable)}
          </span>
        </div>
        <button
          onClick={loadData}
          className="flex items-center gap-2 text-sm text-zinc-400 hover:text-white transition-colors"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      {/* Suggestions List */}
      {error ? (
        <div className="p-8 text-center text-red-400">{error}</div>
      ) : filteredSuggestions.length === 0 ? (
        <div className="p-8 text-center text-zinc-500">
          <CheckCircle size={48} className="mx-auto mb-4 text-emerald-500" />
          <p className="text-lg font-medium text-white">All Clean!</p>
          <p className="text-sm mt-1">No cleanup suggestions at this time.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredSuggestions.map((suggestion) => (
            <SuggestionCard
              key={suggestion.video_id}
              suggestion={suggestion}
              isSelected={selectedIds.has(suggestion.video_id)}
              isProcessing={processingIds.has(suggestion.video_id)}
              onToggleSelect={() => toggleSelect(suggestion.video_id)}
              onAction={(action) => handleAction(suggestion.video_id, action)}
            />
          ))}
        </div>
      )}
    </div>
  );
};

// Sub-components
interface StatCardProps {
  icon: React.ElementType;
  label: string;
  value: string;
  color: string;
}

const StatCard: React.FC<StatCardProps> = ({ icon: Icon, label, value, color }) => (
  <div className="p-4 bg-zinc-900 rounded-xl border border-zinc-800">
    <div className="flex items-center gap-3">
      <div className={`p-2 rounded-lg bg-zinc-800 ${color}`}>
        <Icon size={18} />
      </div>
      <div>
        <p className="text-xs text-zinc-500 uppercase tracking-wider">{label}</p>
        <p className="text-lg font-semibold text-white">{value}</p>
      </div>
    </div>
  </div>
);

interface FilterTabProps {
  label: string;
  count: number;
  isActive: boolean;
  onClick: () => void;
  color?: string;
}

const FilterTab: React.FC<FilterTabProps> = ({ label, count, isActive, onClick, color }) => (
  <button
    onClick={onClick}
    className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm transition-colors ${
      isActive
        ? 'bg-indigo-600/20 text-indigo-400 border border-indigo-500/30'
        : 'bg-zinc-800 text-zinc-400 hover:text-white border border-zinc-700'
    }`}
  >
    <span className={color}>{label}</span>
    <span className="text-xs opacity-70">{count}</span>
  </button>
);

interface SuggestionCardProps {
  suggestion: CleanupSuggestion;
  isSelected: boolean;
  isProcessing: boolean;
  onToggleSelect: () => void;
  onAction: (action: 'delete' | 'keep_forever' | 'dismiss') => void;
}

const SuggestionCard: React.FC<SuggestionCardProps> = ({
  suggestion,
  isSelected,
  isProcessing,
  onToggleSelect,
  onAction,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const Icon = getReasonIcon(suggestion.reason);
  const colorClass = getReasonColor(suggestion.reason);

  return (
    <div
      className={`bg-zinc-900 rounded-xl border transition-all ${
        isSelected ? 'border-indigo-500/50 bg-indigo-600/5' : 'border-zinc-800'
      }`}
    >
      <div className="p-4 flex items-center gap-4">
        {/* Checkbox */}
        <input
          type="checkbox"
          checked={isSelected}
          onChange={onToggleSelect}
          disabled={isProcessing}
          className="w-4 h-4 rounded border-zinc-600 bg-zinc-800 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-0"
        />

        {/* Thumbnail */}
        <div className="w-16 h-16 rounded-lg overflow-hidden bg-zinc-800 flex-shrink-0">
          {suggestion.cover_url ? (
            <img
              src={suggestion.cover_url}
              alt={suggestion.title}
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-zinc-600">
              <AlertTriangle size={24} />
            </div>
          )}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-medium text-white truncate">{suggestion.title || 'Untitled'}</h4>
          <p className="text-xs text-zinc-500 mt-0.5">
            @{suggestion.author || 'Unknown'} • {suggestion.view_count} views
          </p>
          <div className="flex items-center gap-2 mt-2">
            <span className={`inline-flex items-center gap-1 text-xs ${colorClass}`}>
              <Icon size={12} />
              {getReasonLabel(suggestion.reason)}
            </span>
            <span className="text-xs text-zinc-600">•</span>
            <span className="text-xs text-zinc-500">{suggestion.reason_detail}</span>
          </div>
        </div>

        {/* Size */}
        <div className="text-right">
          <p className="text-sm font-medium text-white">
            {formatBytes(suggestion.storage_size || 0)}
          </p>
          <p className="text-xs text-zinc-500">
            {new Date(suggestion.created_at).toLocaleDateString()}
          </p>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2">
          {isProcessing ? (
            <Loader2 size={20} className="text-zinc-500 animate-spin" />
          ) : (
            <>
              <button
                onClick={() => onAction('keep_forever')}
                className="p-2 rounded-lg bg-zinc-800 hover:bg-emerald-600/20 text-zinc-400 hover:text-emerald-400 transition-colors"
                title="Keep Forever"
              >
                <Shield size={16} />
              </button>
              <button
                onClick={() => onAction('dismiss')}
                className="p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors"
                title="Dismiss"
              >
                <XCircle size={16} />
              </button>
              <button
                onClick={() => onAction('delete')}
                className="p-2 rounded-lg bg-zinc-800 hover:bg-red-600/20 text-zinc-400 hover:text-red-400 transition-colors"
                title="Delete"
              >
                <Trash2 size={16} />
              </button>
            </>
          )}
        </div>

        {/* Expand Toggle */}
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="p-1 text-zinc-600 hover:text-zinc-400 transition-colors"
        >
          {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
      </div>

      {/* Expanded Details */}
      {isExpanded && (
        <div className="px-4 pb-4 pt-2 border-t border-zinc-800 ml-8">
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-zinc-500">Created:</span>{' '}
              <span className="text-zinc-300">
                {new Date(suggestion.created_at).toLocaleString()}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Last Viewed:</span>{' '}
              <span className="text-zinc-300">
                {suggestion.last_viewed_at
                  ? new Date(suggestion.last_viewed_at).toLocaleString()
                  : 'Never'}
              </span>
            </div>
            {suggestion.similarity_to && (
              <div className="col-span-2">
                <span className="text-zinc-500">Similar to video ID:</span>{' '}
                <span className="text-zinc-300">#{suggestion.similarity_to}</span>
                <span className="ml-2 text-purple-400">
                  ({Math.round((suggestion.similarity_score || 0) * 100)}% match)
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default CleanupSuggestionsView;
