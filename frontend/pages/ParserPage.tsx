import React, { useState, useEffect } from 'react';
import {
  Link as LinkIcon, AlertCircle, Loader2,
  Layers, Download, CheckCircle2,
  ListVideo, HardDrive, X, ChevronDown, ChevronUp,
  Mic, FileText, Eye,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Video, Tag } from '../types';
import { fetchAllTags as fetchTags, createTag } from '../services/unifiedTagService';
import { MediaCard } from '../components/MediaCard';
import { CompactMediaCard } from '../components/CompactMediaCard';
import { EagleTagPicker } from '../components/EagleTagPicker';
import { TaskMonitor } from '../components/TaskMonitor';
import { useAuth } from '../contexts/AuthContext';
import { useLibraryContext } from '../contexts/LibraryContext';
import { useParser } from '../hooks/useParser';

import { getStorageDisplay } from '../services/systemService';
import {
  useTaskManager,
  formatSpeed as tmFormatSpeed,
  formatFileSize as tmFormatFileSize,
  taskTypeLabel,
  taskTypeIcon,
} from '../contexts/TaskManagerContext';

export function ParserPage() {
  const { t } = useTranslation();
  const { userSettings } = useAuth();
  const [currentResult, setCurrentResult] = useState<Video | null>(null);
  const [showActiveTasks, setShowActiveTasks] = useState(false);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const { activeTasks, cancelTask } = useTaskManager();

  useEffect(() => {
    fetchTags().then(setAllTags).catch(console.error);
  }, []);

  const {
    library, setLibrary, sharedVideoIds,
    loadLibraryData, handleUpdateLibraryItem,
  } = useLibraryContext();

  const {
    urlInput, setUrlInput, parserMode, setParserMode, batchInput, setBatchInput,
    selectedTagIds, setSelectedTagIds,
    isParsing, taskStatus, taskProgress, socketLogs, systemStatus,
    batchResults, error,
    downloadTaskId, downloadStatus, downloadPercent, downloadSpeed,
    handleParse, handleSaveToLibrary, handleBatchSave,
  } = useParser({
    loadLibraryData,
    setLibrary,
    currentResult,
    setCurrentResult,
    isAuthenticated: true,
  });

  // AI intent options — checking one attaches the matching system tag,
  // which the post-download chain (chain_ai_workflows_after_download)
  // turns into an AI workflow: Transcript → transcription,
  // Summary → summary (implies transcript), Analyze → cover analysis.
  const AI_INTENTS = [
    { name: 'Transcript', label: 'Transcript', Icon: Mic },
    { name: 'Summary', label: 'Summary', Icon: FileText },
    { name: 'Analyze', label: 'Analyze', Icon: Eye },
  ] as const;

  const aiTagId = (name: string): string | undefined => {
    const tag =
      allTags.find(tg => tg.name === name && tg.type === 'system') ??
      allTags.find(tg => tg.name === name);
    return tag ? String(tag.id) : undefined;
  };

  const toggleAiIntent = (name: string) => {
    const id = aiTagId(name);
    if (!id) return;
    setSelectedTagIds(prev =>
      prev.includes(id) ? prev.filter(t => t !== id) : [...prev, id]
    );
  };

  return (
    <div className="max-w-3xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="text-center space-y-2 mb-6">
        <h1 className="text-3xl md:text-4xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-indigo-400 to-purple-400">
          {t('parser.title')}
        </h1>
        <p className="text-zinc-400">
          {t('parser.subtitle')}
        </p>
      </div>

      {/* Parsing Mode Toggle */}
      <div className="flex justify-center">
        <div className="bg-zinc-900 p-1 rounded-xl border border-zinc-800 inline-flex">
          <button
            onClick={() => setParserMode('single')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              parserMode === 'single'
              ? 'bg-zinc-800 text-white shadow-sm'
              : 'text-zinc-500 hover:text-zinc-300'
            }`}
          >
            <div className="flex items-center gap-2">
              <LinkIcon size={14} />
              <span>{t('parser.singleLink')}</span>
            </div>
          </button>
          <button
            onClick={() => setParserMode('batch')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              parserMode === 'batch'
              ? 'bg-zinc-800 text-white shadow-sm'
              : 'text-zinc-500 hover:text-zinc-300'
            }`}
          >
            <div className="flex items-center gap-2">
              <Layers size={14} />
              <span>{t('parser.batchDownload')}</span>
            </div>
          </button>
        </div>
      </div>

      <div className="relative group">
        <div className="absolute -inset-0.5 bg-gradient-to-r from-indigo-500 to-purple-600 rounded-xl opacity-30 group-hover:opacity-60 transition duration-500 blur"></div>
        <div className="relative bg-zinc-900 rounded-xl p-2 border border-zinc-800 shadow-xl">
          {parserMode === 'single' ? (
            <div className="flex items-center">
              <LinkIcon className="ml-3 text-zinc-500 w-5 h-5 flex-shrink-0" />
              <input
                type="text"
                placeholder={t('parser.placeholder')}
                className="flex-1 bg-transparent border-none outline-none text-zinc-200 placeholder-zinc-600 px-4 py-3"
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleParse()}
              />
              <button
                onClick={handleParse}
                disabled={isParsing || !urlInput}
                className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-6 py-2.5 rounded-lg font-medium transition-all shadow-lg shadow-indigo-500/20 flex items-center gap-2 flex-shrink-0"
              >
                {isParsing && taskProgress < 100 ? <Loader2 className="animate-spin w-4 h-4" /> : t('parser.analyze')}
              </button>
            </div>
          ) : (
            <div className="flex flex-col">
              <textarea
                placeholder={t('parser.batchPlaceholder')}
                rows={5}
                className="w-full bg-transparent border-none outline-none text-zinc-200 placeholder-zinc-600 px-4 py-3 resize-none font-mono text-sm"
                value={batchInput}
                onChange={(e) => setBatchInput(e.target.value)}
              />
              <div className="flex justify-between items-center px-2 py-2 border-t border-zinc-800">
                <span className="text-xs text-zinc-500 ml-2">
                   {batchInput.split(/\r?\n/).filter(l => l.trim().length > 0).length} links detected
                </span>
                <button
                  onClick={handleParse}
                  disabled={isParsing || !batchInput}
                  className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-6 py-2 rounded-lg font-medium transition-all shadow-lg shadow-indigo-500/20 flex items-center gap-2"
                >
                  {isParsing && taskProgress < 100 ? <Loader2 className="animate-spin w-4 h-4" /> : t('parser.processBatch')}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* AI Processing — checking one attaches the system tag that
          triggers the matching post-download AI workflow */}
      <div className="bg-zinc-900/50 border border-zinc-800 rounded-xl p-3">
        <div className="text-xs font-medium text-zinc-400 mb-2 px-1">AI Processing</div>
        <div className="grid grid-cols-3 gap-2">
          {AI_INTENTS.map(({ name, label, Icon }) => {
            const id = aiTagId(name);
            const checked = id ? selectedTagIds.includes(id) : false;
            return (
              <button
                key={name}
                type="button"
                disabled={!id}
                onClick={() => toggleAiIntent(name)}
                className={`flex items-center justify-center gap-2 px-3 py-2 rounded-lg border text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed ${
                  checked
                    ? 'border-indigo-500/60 bg-indigo-500/10 text-indigo-300'
                    : 'border-zinc-800 bg-zinc-900/50 text-zinc-500 hover:border-zinc-700'
                }`}
              >
                <Icon size={14} />
                <span>{label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Tag Selector */}
      <EagleTagPicker
        selectedTagIds={selectedTagIds}
        onTagsChange={setSelectedTagIds}
        allTags={allTags}
        onCreate={async (name, color) => {
          try {
            const tag = await createTag({ name, color, type: 'user' });
            setAllTags(prev => [...prev, tag]);
            return tag as any;
          } catch { return null; }
        }}
      />

      {error && (
        <div className="bg-red-950/20 border border-red-900/50 text-red-200 p-4 rounded-xl flex items-center gap-3">
          <AlertCircle className="w-5 h-5 flex-shrink-0" />
          <p>{error}</p>
        </div>
      )}

      {/* Task Monitor */}
      {(isParsing || taskProgress > 0) && taskProgress < 100 && (
        <TaskMonitor
          logs={socketLogs}
          progress={taskProgress}
          status={taskStatus}
          systemStatus={systemStatus}
          downloadSpeed={downloadSpeed}
        />
      )}

      {/* Result Card */}
      {parserMode === 'single' && currentResult && (
        <div className="mt-8 animate-in fade-in zoom-in-95 duration-300">
           <div className="flex items-center justify-between mb-4 px-1">
              <h3 className="text-lg font-semibold text-zinc-300">Analysis Result</h3>
              <div className="flex items-center gap-2">
                 {downloadTaskId && downloadStatus !== 'completed' && downloadStatus !== 'failed' ? (
                   <span className="text-xs text-indigo-400 flex items-center gap-1">
                      <Loader2 size={12} className="animate-spin" />
                      {t('download.downloading')} {downloadPercent}%
                      {downloadSpeed && <span className="text-zinc-500 ml-1">({downloadSpeed})</span>}
                   </span>
                 ) : downloadStatus === 'completed' || taskProgress === 100 ? (
                   <span className="text-xs text-green-400 flex items-center gap-1">
                      <CheckCircle2 size={12} />
                      {t('download.completed')}
                   </span>
                 ) : downloadStatus === 'failed' ? (
                   <span className="text-xs text-red-400 flex items-center gap-1">
                      <AlertCircle size={12} />
                      {t('download.failed')}
                   </span>
                 ) : null}
              </div>
           </div>

           <MediaCard
             data={currentResult}
             onUpdate={handleUpdateLibraryItem}
             downloadStatus={downloadTaskId ? downloadStatus : undefined}
             downloadPercent={downloadPercent}
             downloadSpeed={downloadSpeed}
             progressStyle={userSettings.progressStyle || 'neon'}
           />
        </div>
      )}

      {/* Batch Results */}
      {parserMode === 'batch' && batchResults.length > 0 && (
         <div className="mt-8 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="flex items-center justify-between mb-4 px-1">
                <div>
                  <h3 className="text-lg font-semibold text-zinc-300">Batch Results</h3>
                  <p className="text-xs text-zinc-500">Successfully processed {batchResults.length} items.</p>
                </div>
                <button
                  onClick={handleBatchSave}
                  className="bg-zinc-100 hover:bg-white text-zinc-900 px-4 py-2 rounded-lg text-sm font-semibold transition-colors flex items-center gap-2 shadow-lg"
                >
                   <Download size={16} />
                   Save All ({batchResults.length})
                </button>
            </div>

            <div className="columns-1 sm:columns-2 md:columns-3 gap-3 mx-auto space-y-3">
                {batchResults.map((item, idx) => (
                  <CompactMediaCard
                    key={`${item.platform_id}-${idx}`}
                    data={item}
                    onClick={() => {
                    }}
                    isShared={sharedVideoIds.includes(item.platform_id)}
                  />
                ))}
            </div>
         </div>
      )}

      {!currentResult && batchResults.length === 0 && !isParsing && taskProgress === 0 && (
        <>
        <div className="grid grid-cols-3 gap-2 sm:gap-3 mt-8">
           <button
              onClick={() => setShowActiveTasks(prev => !prev)}
              className={`p-3 md:p-5 rounded-xl bg-zinc-900/50 border transition-colors text-center flex flex-col items-center justify-center min-h-0 cursor-pointer ${
                showActiveTasks
                  ? 'border-indigo-500/60 ring-1 ring-indigo-500/20' :
                systemStatus?.queue.status === 'offline'
                  ? 'border-red-800/60' : 'border-zinc-800/50 hover:border-zinc-700'
              }`}
           >
              <div className={`w-8 h-8 md:w-10 md:h-10 rounded-lg flex items-center justify-center mb-2 ${
                systemStatus?.queue.status === 'offline'
                  ? 'bg-red-900/30 text-red-400' :
                activeTasks.length > 0 ? 'bg-indigo-900/30 text-indigo-400' : 'bg-zinc-800/50 text-zinc-500'
              }`}>
                <ListVideo size={16} className="md:hidden" />
                <ListVideo size={20} className="hidden md:block" />
              </div>
              <h4 className="font-semibold text-zinc-200 text-xs md:text-base mb-0.5">Engine</h4>
              <p className={`text-xs md:text-sm font-mono ${
                systemStatus?.queue.status === 'offline'
                  ? 'text-red-400' :
                activeTasks.length > 0 ? 'text-indigo-400' : 'text-green-400'
              }`}>
                {systemStatus?.queue.status === 'offline' ? 'Offline' :
                 activeTasks.length > 0 ? `${activeTasks.length} Running` : 'Idle'}
              </p>
              {systemStatus?.queue.status === 'offline' && (
                <p className="text-[10px] md:text-xs text-red-400/80 mt-0.5">DBOS unavailable</p>
              )}
              {systemStatus?.queue.status === 'online' && systemStatus.queue.pending > 0 && (
                <p className="text-[10px] md:text-xs text-zinc-600 mt-0.5">
                  {systemStatus.queue.pending} queued
                </p>
              )}
              <div className="mt-1 text-zinc-500">
                {showActiveTasks ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </div>
           </button>
           <div className="p-3 md:p-5 rounded-xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center flex flex-col items-center justify-center min-h-0">
              <div className={`w-8 h-8 md:w-10 md:h-10 rounded-lg flex items-center justify-center mb-2 ${
                systemStatus?.storage.status === 'ok' ? 'bg-purple-900/30 text-purple-400' :
                systemStatus?.storage.status === 'warning' ? 'bg-yellow-900/30 text-yellow-400' :
                systemStatus?.storage.status === 'critical' ? 'bg-red-900/30 text-red-400' : 'bg-zinc-800/50 text-zinc-500'
              }`}>
                <HardDrive size={16} className="md:hidden" />
                <HardDrive size={20} className="hidden md:block" />
              </div>
              <h4 className="font-semibold text-zinc-200 text-xs md:text-base mb-0.5">Storage</h4>
              <p className={`text-xs md:text-sm font-mono ${
                systemStatus?.storage.status === 'ok' ? 'text-purple-400' :
                systemStatus?.storage.status === 'warning' ? 'text-yellow-400' :
                systemStatus?.storage.status === 'critical' ? 'text-red-400' : 'text-zinc-500'
              }`}>
                {systemStatus ? getStorageDisplay(systemStatus.storage) : '...'}
              </p>
              {systemStatus?.storage.percent_used ? (
                <p className="text-[10px] md:text-xs text-zinc-600 mt-0.5">{systemStatus.storage.percent_used}% used</p>
              ) : null}
           </div>
        </div>

        {/* Active Tasks Panel — shown when Engine card is clicked */}
        {showActiveTasks && (
          <div className="mt-3 bg-zinc-900/50 border border-zinc-800 rounded-xl overflow-hidden animate-in slide-in-from-top-2 duration-200">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
              <span className="text-sm font-semibold text-zinc-200">Active Tasks</span>
              <span className="text-xs text-zinc-500">{activeTasks.length} running</span>
            </div>
            {activeTasks.length > 0 ? (
              <div className="max-h-60 overflow-y-auto divide-y divide-zinc-800/50">
                {activeTasks.map((task) => (
                  <div key={task.id} className="px-4 py-3 flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-sm bg-indigo-500/20 text-indigo-400">
                      {taskTypeIcon(task.task_type)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-zinc-300 truncate max-w-[240px]">{task.title}</span>
                        <div className="flex items-center gap-1.5 shrink-0 ml-2">
                          {task.progress > 0 && (
                            <span className="text-[10px] text-zinc-500">{task.progress}%</span>
                          )}
                          {task.speed != null && task.speed > 0 && (
                            <span className="text-[10px] text-zinc-600">{tmFormatSpeed(task.speed)}</span>
                          )}
                          <button
                            onClick={(e) => { e.stopPropagation(); cancelTask(task.id); }}
                            className="p-0.5 rounded text-zinc-600 hover:text-red-400 transition-colors"
                            title="Cancel"
                          >
                            <X size={12} />
                          </button>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[10px] text-zinc-600">{taskTypeLabel(task.task_type)}</span>
                        {task.subtitle && (
                          <span className="text-[10px] text-zinc-600 truncate">{task.subtitle}</span>
                        )}
                        {task.total_bytes != null && task.total_bytes > 0 && (
                          <span className="text-[10px] text-zinc-600">{tmFormatFileSize(task.total_bytes)}</span>
                        )}
                      </div>
                      <div className="mt-1.5 h-1 bg-zinc-800 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-300 bg-indigo-500"
                          style={{ width: `${Math.max(task.progress, task.status === 'processing' ? 2 : 0)}%` }}
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-8 text-zinc-500">
                <CheckCircle2 size={20} className="mb-1.5 text-zinc-600" />
                <span className="text-xs">No active tasks</span>
              </div>
            )}
          </div>
        )}
        </>
      )}

    </div>
  );
}
