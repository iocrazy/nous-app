/**
 * Debug-only performance overlay — shows timing breakdown on mobile.
 * Remove this component once debugging is complete.
 */
import React, { useState, useEffect } from 'react';
import { getPerfEntries } from '../utils/perfLog';

export function PerfOverlay() {
  const [visible, setVisible] = useState(false);
  const [entries, setEntries] = useState(getPerfEntries());

  useEffect(() => {
    const interval = setInterval(() => {
      setEntries(getPerfEntries());
    }, 500);
    return () => clearInterval(interval);
  }, []);

  if (!visible) {
    return (
      <button
        onClick={() => setVisible(true)}
        className="fixed top-1 left-1 z-[99999] bg-red-600 text-white text-[10px] px-1.5 py-0.5 rounded opacity-60"
      >
        PERF
      </button>
    );
  }

  return (
    <div className="fixed top-0 left-0 right-0 z-[99999] bg-black/90 text-green-400 text-[10px] font-mono p-2 max-h-[50vh] overflow-y-auto">
      <div className="flex justify-between mb-1">
        <span className="text-white font-bold">Performance Log</span>
        <button onClick={() => setVisible(false)} className="text-red-400">CLOSE</button>
      </div>
      {entries.length === 0 ? (
        <div className="text-zinc-500">No entries yet...</div>
      ) : (
        entries.map((e, i) => (
          <div key={i} className="flex gap-2">
            <span className="text-yellow-400 w-14 text-right shrink-0">{e.time}ms</span>
            <span className="text-zinc-500 w-12 text-right shrink-0">+{e.delta}ms</span>
            <span>{e.label}</span>
          </div>
        ))
      )}
    </div>
  );
}
