import React, { useState } from 'react';
import {
  Link as LinkIcon, AlertCircle, Loader2,
  Check, Music, Video as VideoIcon, Image as ImageIcon,
  Layers, Download, CheckCircle2,
  ListVideo, HardDrive,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Video } from '../types';
import { MediaCard } from '../components/MediaCard';
import { CompactMediaCard } from '../components/CompactMediaCard';
import { ParseModeCard } from '../components/ParseModeCard';
import { ParserTagSelector } from '../components/ParserTagSelector';
import { TaskMonitor } from '../components/TaskMonitor';
import { useAuth } from '../contexts/AuthContext';
import { useLibrary } from '../hooks/useLibrary';
import { useParser } from '../hooks/useParser';
import { useTeamContext } from '../contexts/TeamContext';
import { getQueueDisplay, getStorageDisplay } from '../services/systemService';

export function ParserPage() {
  const { t } = useTranslation();
  const { userSettings } = useAuth();
  const { selectedTeamId } = useTeamContext();

  const [currentResult, setCurrentResult] = useState<Video | null>(null);

  const {
    library, setLibrary, sharedVideoIds,
    loadLibraryData, handleUpdateLibraryItem,
  } = useLibrary({ isAuthenticated: true, selectedTeamId });

  const {
    urlInput, setUrlInput, parserMode, setParserMode, batchInput, setBatchInput,
    downloadOptions, setDownloadOptions, selectedTagIds, setSelectedTagIds,
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

      {/* Parser Configuration Options */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <button
          onClick={() => setDownloadOptions(prev => ({ ...prev, video: !prev.video }))}
          className={`flex items-center gap-3 p-3.5 rounded-xl border transition-all text-left ${
            downloadOptions.video
              ? 'bg-indigo-500/10 border-indigo-500/40 text-indigo-300'
              : 'bg-zinc-900/50 border-zinc-800 text-zinc-500 hover:border-zinc-700'
          }`}
        >
          <div className={`w-5 h-5 rounded flex items-center justify-center border flex-shrink-0 transition-colors ${
            downloadOptions.video ? 'bg-indigo-500 border-indigo-500' : 'border-zinc-600 bg-zinc-900'
          }`}>
            {downloadOptions.video && <Check size={14} className="text-white" />}
          </div>
          <div className="flex items-center gap-2">
            <VideoIcon size={16} />
            <span className="text-sm font-medium">{t('parser.downloadVideo')}</span>
          </div>
        </button>

        <button
          onClick={() => setDownloadOptions(prev => ({ ...prev, audio: !prev.audio }))}
          className={`flex items-center gap-3 p-3.5 rounded-xl border transition-all text-left ${
            downloadOptions.audio
              ? 'bg-indigo-500/10 border-indigo-500/40 text-indigo-300'
              : 'bg-zinc-900/50 border-zinc-800 text-zinc-500 hover:border-zinc-700'
          }`}
        >
          <div className={`w-5 h-5 rounded flex items-center justify-center border flex-shrink-0 transition-colors ${
            downloadOptions.audio ? 'bg-indigo-500 border-indigo-500' : 'border-zinc-600 bg-zinc-900'
          }`}>
            {downloadOptions.audio && <Check size={14} className="text-white" />}
          </div>
          <div className="flex items-center gap-2">
            <Music size={16} />
            <span className="text-sm font-medium">{t('parser.downloadAudio')}</span>
          </div>
        </button>

        <button
          onClick={() => setDownloadOptions(prev => ({ ...prev, cover: !prev.cover }))}
          className={`flex items-center gap-3 p-3.5 rounded-xl border transition-all text-left ${
            downloadOptions.cover
              ? 'bg-indigo-500/10 border-indigo-500/40 text-indigo-300'
              : 'bg-zinc-900/50 border-zinc-800 text-zinc-500 hover:border-zinc-700'
          }`}
        >
          <div className={`w-5 h-5 rounded flex items-center justify-center border flex-shrink-0 transition-colors ${
            downloadOptions.cover ? 'bg-indigo-500 border-indigo-500' : 'border-zinc-600 bg-zinc-900'
          }`}>
            {downloadOptions.cover && <Check size={14} className="text-white" />}
          </div>
          <div className="flex items-center gap-2">
            <ImageIcon size={16} />
            <span className="text-sm font-medium">{t('parser.downloadCover')}</span>
          </div>
        </button>
      </div>

      {/* Tag Selector */}
      <ParserTagSelector
        selectedTagIds={selectedTagIds}
        onTagsChange={setSelectedTagIds}
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
             onSave={(item) => handleSaveToLibrary(item)}
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

            <div className="columns-2 md:columns-3 gap-3 mx-auto space-y-3">
                {batchResults.map((item, idx) => (
                  <CompactMediaCard
                    key={`${item.platform_id}-${idx}`}
                    data={item}
                    onClick={() => {
                       console.log("Clicked batch item:", item.title);
                    }}
                    isShared={sharedVideoIds.includes(item.platform_id)}
                  />
                ))}
            </div>
         </div>
      )}

      {!currentResult && batchResults.length === 0 && !isParsing && taskProgress === 0 && (
        <div className="grid grid-cols-3 gap-3 mt-8">
           <div className="p-3 md:p-5 rounded-xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center flex flex-col items-center justify-center min-h-0">
              <div className={`w-8 h-8 md:w-10 md:h-10 rounded-lg flex items-center justify-center mb-2 ${
                systemStatus?.queue.status === 'offline' ? 'bg-red-900/30 text-red-400' :
                systemStatus?.queue.active ? 'bg-indigo-900/30 text-indigo-400' : 'bg-zinc-800/50 text-zinc-500'
              }`}>
                <ListVideo size={16} className="md:hidden" />
                <ListVideo size={20} className="hidden md:block" />
              </div>
              <h4 className="font-semibold text-zinc-200 text-xs md:text-base mb-0.5">Queue</h4>
              <p className={`text-xs md:text-sm font-mono ${
                systemStatus?.queue.status === 'offline' ? 'text-red-400' :
                systemStatus?.queue.active ? 'text-indigo-400' : 'text-zinc-500'
              }`}>
                {systemStatus ? getQueueDisplay(systemStatus.queue) : '...'}
              </p>
              {systemStatus?.queue.pending ? (
                <p className="text-[10px] md:text-xs text-zinc-600 mt-0.5">{systemStatus.queue.pending} pending</p>
              ) : null}
           </div>
           <ParseModeCard />
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
      )}
    </div>
  );
}
