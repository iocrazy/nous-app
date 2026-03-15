import React, { useEffect, useRef } from 'react';
import { ListVideo, Wifi, HardDrive } from 'lucide-react';
import { SystemStatus, getQueueDisplay, getStorageDisplay } from '../services/systemService';

export interface LogEntry {
  id: string;
  time: string;
  message: string;
  type: 'info' | 'success' | 'warning' | 'error';
}

export const TaskMonitor = ({
  logs,
  progress,
  status,
  systemStatus,
  downloadSpeed,
}: {
  logs: LogEntry[],
  progress: number,
  status: string,
  systemStatus: SystemStatus | null,
  downloadSpeed?: string,
}) => {
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  return (
    <div className="mt-8 bg-zinc-950 border border-zinc-800 rounded-xl overflow-hidden shadow-2xl animate-in slide-in-from-bottom-2 duration-300">
      {/* Header / Status Bar */}
      <div className="bg-zinc-900/80 backdrop-blur-sm border-b border-zinc-800 p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
             <div className="relative">
                {progress === 100 ? (
                  <div className="w-3 h-3 bg-green-500 rounded-full shadow-[0_0_10px_rgba(34,197,94,0.5)]"></div>
                ) : (
                  <div className="w-3 h-3 bg-indigo-500 rounded-full animate-pulse shadow-[0_0_10px_rgba(99,102,241,0.5)]"></div>
                )}
             </div>
             <span className="font-mono text-sm text-zinc-200 font-semibold uppercase tracking-wider">
               {status}
             </span>
          </div>
          <span className="text-xs font-mono text-zinc-500">{progress}%</span>
        </div>

        {/* Progress Bar */}
        <div className="h-1.5 w-full bg-zinc-800 rounded-full overflow-hidden">
           <div
             className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all duration-300 ease-out relative"
             style={{ width: `${progress}%` }}
           >
              <div className="absolute inset-0 bg-white/20 animate-[shimmer_2s_infinite]"></div>
           </div>
        </div>

        {/* Quick Stats Grid */}
        <div className="grid grid-cols-3 gap-4 mt-4 pt-4 border-t border-zinc-800/50">
           <div className="flex flex-col items-center">
              <span className="text-[10px] text-zinc-500 uppercase tracking-widest mb-1">Worker</span>
              <div className="flex items-center gap-1.5 text-zinc-300">
                 <ListVideo size={14} className={
                   systemStatus?.queue.status === 'offline' || systemStatus?.queue.status === 'outdated'
                     ? 'text-red-400' :
                   systemStatus?.queue.active ? 'text-indigo-400' : 'text-green-400'
                 } />
                 <span className={`font-mono text-xs font-medium ${
                   systemStatus?.queue.status === 'offline' || systemStatus?.queue.status === 'outdated'
                     ? 'text-red-400' : ''
                 }`}>
                   {systemStatus ? getQueueDisplay(systemStatus.queue) : '...'}
                 </span>
              </div>
           </div>
           <div className="flex flex-col items-center border-l border-zinc-800/50">
              <span className="text-[10px] text-zinc-500 uppercase tracking-widest mb-1">Network</span>
              <div className="flex items-center gap-1.5 text-zinc-300">
                 <Wifi size={14} className={
                   downloadSpeed || systemStatus?.network.status === 'active' ? 'text-emerald-400' :
                   systemStatus?.network.status === 'error' ? 'text-red-400' : 'text-zinc-500'
                 } />
                 <span className="font-mono text-xs font-medium">
                   {downloadSpeed || systemStatus?.network.speed || '0 B/s'}
                 </span>
              </div>
           </div>
           <div className="flex flex-col items-center border-l border-zinc-800/50">
              <span className="text-[10px] text-zinc-500 uppercase tracking-widest mb-1">Storage</span>
              <div className="flex items-center gap-1.5 text-zinc-300">
                 <HardDrive size={14} className={
                   systemStatus?.storage.status === 'ok' ? 'text-purple-400' :
                   systemStatus?.storage.status === 'warning' ? 'text-yellow-400' :
                   systemStatus?.storage.status === 'critical' ? 'text-red-400' : 'text-zinc-500'
                 } />
                 <span className="font-mono text-xs font-medium">
                   {systemStatus ? getStorageDisplay(systemStatus.storage) : '...'}
                 </span>
              </div>
           </div>
        </div>
      </div>

      {/* Terminal Log View */}
      <div className="bg-[#0c0c0e] p-4 h-48 overflow-y-auto font-mono text-xs space-y-1.5 custom-scrollbar">
        {logs.length === 0 && (
          <div className="text-zinc-700 italic">Waiting for task logs...</div>
        )}
        {logs.map((log) => (
          <div key={log.id} className="flex gap-3 hover:bg-white/5 p-0.5 rounded px-2 transition-colors">
            <span className="text-zinc-600 flex-shrink-0">[{log.time}]</span>
            <span className={`break-all ${
              log.type === 'error' ? 'text-red-400' :
              log.type === 'success' ? 'text-green-400' :
              log.type === 'warning' ? 'text-yellow-400' :
              'text-zinc-300'
            }`}>
              {log.type === 'success' && '✓ '}
              {log.type === 'error' && '✗ '}
              {log.message}
            </span>
          </div>
        ))}
        <div ref={logsEndRef} />
      </div>
    </div>
  );
};
