/**
 * LogsPanel Component - User activity logs management
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  RefreshCw,
  Copy,
  Download,
  Search,
  ChevronLeft,
  ChevronRight,
  FileText,
  CheckCircle,
  AlertCircle,
  AlertTriangle,
  Info,
  Clock,
  Filter,
  Calendar,
  Loader2,
} from 'lucide-react';

// Types
interface LogEntry {
  id: string;
  action: string;
  message: string;
  status: string;
  platform_id?: string;
  details?: Record<string, unknown>;
  created_at: string;
}

interface LogsResponse {
  success: boolean;
  logs: LogEntry[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

import { getAuthHeaders } from '../services/parserService';

// API configuration
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';  // Empty string for relative paths via proxy
  }
  return 'http://localhost:8080';
};

// Level options
const LEVEL_OPTIONS = [
  { value: '', label: 'All Levels' },
  { value: 'info,success', label: 'INFO' },
  { value: 'warning', label: 'WARN' },
  { value: 'error', label: 'ERROR' },
  { value: 'pending', label: 'PENDING' },
];

// Date range options
const DATE_RANGE_OPTIONS = [
  { value: '7days', label: 'Last 7 days' },
  { value: 'today', label: 'Today' },
  { value: '30days', label: 'Last 30 days' },
  { value: 'custom', label: 'Custom range' },
];

// Page size options
const PAGE_SIZE_OPTIONS = [50, 100, 200];

// Status to display mapping
const getStatusDisplay = (status: string): { label: string; color: string; icon: React.ElementType } => {
  switch (status) {
    case 'success':
      return { label: 'INFO', color: 'text-green-400 bg-green-500/10 border-green-500/20', icon: CheckCircle };
    case 'info':
      return { label: 'INFO', color: 'text-blue-400 bg-blue-500/10 border-blue-500/20', icon: Info };
    case 'warning':
      return { label: 'WARN', color: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20', icon: AlertTriangle };
    case 'error':
      return { label: 'ERROR', color: 'text-red-400 bg-red-500/10 border-red-500/20', icon: AlertCircle };
    case 'pending':
      return { label: 'PENDING', color: 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20', icon: Clock };
    default:
      return { label: 'INFO', color: 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20', icon: Info };
  }
};

// Format timestamp
const formatTime = (isoString: string): string => {
  const date = new Date(isoString);
  return date.toLocaleTimeString('en-US', { hour12: false });
};

const formatDate = (isoString: string): string => {
  const date = new Date(isoString);
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

export const LogsPanel: React.FC = () => {
  // State
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedAll, setCopiedAll] = useState(false);

  // Filters
  const [level, setLevel] = useState('');
  const [dateRange, setDateRange] = useState('7days');
  const [customStartDate, setCustomStartDate] = useState('');
  const [customEndDate, setCustomEndDate] = useState('');
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');

  // Pagination
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  // Fetch logs
  const fetchLogs = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const apiUrl = getApiUrl();
      const params = new URLSearchParams();

      if (level) params.append('level', level);
      if (dateRange !== 'custom') {
        params.append('date_range', dateRange);
      } else {
        if (customStartDate) params.append('start_date', customStartDate);
        if (customEndDate) params.append('end_date', customEndDate);
      }
      if (search) params.append('search', search);
      params.append('page', page.toString());
      params.append('page_size', pageSize.toString());

      const response = await fetch(`${apiUrl}/api/v1/logs?${params}`, {
        headers: getAuthHeaders(),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Failed to fetch logs' }));
        throw new Error(errorData.detail || `HTTP ${response.status}`);
      }

      const data: LogsResponse = await response.json();
      setLogs(data.logs);
      setTotal(data.total);
      setTotalPages(data.total_pages);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch logs');
    } finally {
      setIsLoading(false);
    }
  }, [level, dateRange, customStartDate, customEndDate, search, page, pageSize]);

  // Initial fetch and refetch on filter changes
  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  // Reset page when filters change
  useEffect(() => {
    setPage(1);
  }, [level, dateRange, customStartDate, customEndDate, search, pageSize]);

  // Handle search submit
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSearch(searchInput);
  };

  // Handle refresh
  const handleRefresh = () => {
    fetchLogs();
  };

  // Handle copy all logs
  const handleCopyAll = async () => {
    const text = logs
      .map(log => {
        const time = formatTime(log.created_at);
        const status = getStatusDisplay(log.status);
        return `${time} [${status.label}] ${log.message}`;
      })
      .join('\n');

    await navigator.clipboard.writeText(text);
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 2000);
  };

  // Handle export
  const handleExport = async (format: 'json' | 'csv') => {
    try {
      const apiUrl = getApiUrl();
      const params = new URLSearchParams();

      params.append('format', format);
      if (level) params.append('level', level);
      if (dateRange !== 'custom') {
        params.append('date_range', dateRange);
      } else {
        if (customStartDate) params.append('start_date', customStartDate);
        if (customEndDate) params.append('end_date', customEndDate);
      }
      if (search) params.append('search', search);

      const response = await fetch(`${apiUrl}/api/v1/logs/export?${params}`, {
        headers: getAuthHeaders(),
      });

      if (!response.ok) {
        throw new Error('Export failed');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `logs_export.${format}`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      setError('Failed to export logs');
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 bg-amber-500/10 rounded-lg text-amber-400">
          <FileText size={20} />
        </div>
        <div>
          <h2 className="font-semibold text-zinc-200">Activity Logs</h2>
          <p className="text-sm text-zinc-500">View your activity history</p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Level Filter */}
        <div className="relative">
          <Filter size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="bg-zinc-950 border border-zinc-800 rounded-lg pl-9 pr-8 py-2 text-sm text-zinc-300 outline-none focus:border-indigo-500 transition-colors appearance-none cursor-pointer"
          >
            {LEVEL_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {/* Date Range Filter */}
        <div className="relative">
          <Calendar size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
          <select
            value={dateRange}
            onChange={(e) => setDateRange(e.target.value)}
            className="bg-zinc-950 border border-zinc-800 rounded-lg pl-9 pr-8 py-2 text-sm text-zinc-300 outline-none focus:border-indigo-500 transition-colors appearance-none cursor-pointer"
          >
            {DATE_RANGE_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {/* Custom Date Range */}
        {dateRange === 'custom' && (
          <>
            <input
              type="date"
              value={customStartDate}
              onChange={(e) => setCustomStartDate(e.target.value)}
              className="bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-300 outline-none focus:border-indigo-500 transition-colors"
            />
            <span className="text-zinc-500">to</span>
            <input
              type="date"
              value={customEndDate}
              onChange={(e) => setCustomEndDate(e.target.value)}
              className="bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-300 outline-none focus:border-indigo-500 transition-colors"
            />
          </>
        )}

        {/* Search */}
        <form onSubmit={handleSearchSubmit} className="flex-1 min-w-[200px]">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search logs..."
              className="w-full bg-zinc-950 border border-zinc-800 rounded-lg pl-9 pr-4 py-2 text-sm text-zinc-300 outline-none focus:border-indigo-500 transition-colors placeholder:text-zinc-600"
            />
          </div>
        </form>
      </div>

      {/* Logs List */}
      <div className="bg-zinc-950 border border-zinc-800 rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 size={24} className="animate-spin text-indigo-400" />
          </div>
        ) : error ? (
          <div className="flex items-center justify-center py-16 text-red-400">
            <AlertCircle size={18} className="mr-2" />
            {error}
          </div>
        ) : logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-zinc-500">
            <FileText size={32} className="mb-2 opacity-50" />
            <p>No logs found</p>
          </div>
        ) : (
          <div className="divide-y divide-zinc-800/50 max-h-[500px] overflow-y-auto">
            {logs.map((log) => {
              const statusDisplay = getStatusDisplay(log.status);
              const StatusIcon = statusDisplay.icon;

              return (
                <div
                  key={log.id}
                  className="flex items-start gap-3 px-4 py-3 hover:bg-zinc-900/50 transition-colors"
                >
                  {/* Time */}
                  <div className="flex-shrink-0 text-xs text-zinc-500 font-mono w-20">
                    <div>{formatTime(log.created_at)}</div>
                    <div className="text-zinc-600">{formatDate(log.created_at)}</div>
                  </div>

                  {/* Status Badge */}
                  <div
                    className={`flex-shrink-0 px-2 py-0.5 rounded text-xs font-medium border flex items-center gap-1 ${statusDisplay.color}`}
                  >
                    <StatusIcon size={10} />
                    {statusDisplay.label}
                  </div>

                  {/* Message */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-zinc-300 break-words">{log.message}</p>
                    {log.platform_id && (
                      <p className="text-xs text-zinc-600 mt-0.5 font-mono">
                        Video: {log.platform_id}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Pagination & Actions */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        {/* Info */}
        <div className="text-sm text-zinc-500">
          Showing {logs.length} of {total} logs
        </div>

        {/* Page Size */}
        <div className="flex items-center gap-2">
          <span className="text-sm text-zinc-500">Per page:</span>
          <select
            value={pageSize}
            onChange={(e) => setPageSize(Number(e.target.value))}
            className="bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-300 outline-none focus:border-indigo-500 transition-colors"
          >
            {PAGE_SIZE_OPTIONS.map(size => (
              <option key={size} value={size}>{size}</option>
            ))}
          </select>
        </div>

        {/* Pagination */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="p-2 rounded-lg border border-zinc-800 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-sm text-zinc-400 px-2">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="p-2 rounded-lg border border-zinc-800 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronRight size={16} />
          </button>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2">
          <button
            onClick={handleRefresh}
            disabled={isLoading}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-zinc-800 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors text-sm"
          >
            <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
            Refresh
          </button>

          <button
            onClick={handleCopyAll}
            disabled={logs.length === 0}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-zinc-800 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors text-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {copiedAll ? <CheckCircle size={14} className="text-green-400" /> : <Copy size={14} />}
            {copiedAll ? 'Copied!' : 'Copy'}
          </button>

          {/* Export Dropdown */}
          <div className="relative group">
            <button
              disabled={logs.length === 0}
              className="flex items-center gap-2 px-3 py-2 rounded-lg border border-zinc-800 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors text-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Download size={14} />
              Export
            </button>
            <div className="absolute right-0 top-full mt-1 bg-zinc-900 border border-zinc-800 rounded-lg shadow-xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-10">
              <button
                onClick={() => handleExport('json')}
                className="block w-full text-left px-4 py-2 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors rounded-t-lg"
              >
                Export as JSON
              </button>
              <button
                onClick={() => handleExport('csv')}
                className="block w-full text-left px-4 py-2 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors rounded-b-lg"
              >
                Export as CSV
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LogsPanel;
