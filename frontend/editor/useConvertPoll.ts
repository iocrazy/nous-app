/**
 * useConvertPoll — a reusable "dispatch a slow chapter workflow, then poll until
 * its result materialises" driver (Phase B Task 3, extracted from EditorShell's
 * convert-to-scenes polling — behaviour unchanged).
 *
 * Convert / Expand / Branch are backend workflows that return a task id and land
 * their output (scenes for convert, child chapters for expand/branch) some
 * seconds later. Rather than subscribe to a task row, we poll the authoritative
 * lists and stop when a caller-supplied predicate over the fresh scenes+chapters
 * holds (or the attempt cap is hit). This observes the exact end state the UI
 * cares about, needs no realtime subscription, and is self-contained.
 */
import { useCallback, useEffect, useRef } from 'react';
import { listScenes } from './sceneService';
import { fetchScriptProject } from '../services/scriptService';
import type { SceneDoc } from './types';
import type { ScriptChapter } from '../types';

/** Poll cadence + cap while waiting for a workflow's output to appear. */
export const CONVERT_POLL_MS = 5000;
export const CONVERT_POLL_MAX = 12;

export interface PollData {
  scenes: SceneDoc[];
  chapters: ScriptChapter[];
}
export type PollPredicate = (data: PollData) => boolean;
export type PollSettled = (settled: boolean, data: PollData) => void;

export interface ConvertPoll {
  /**
   * Begin polling. `predicate` decides when the workflow's output has landed;
   * `onSettled` fires once — with `settled=true` when the predicate matched, or
   * `settled=false` when the attempt cap was reached first.
   */
  startPoll: (predicate: PollPredicate, onSettled?: PollSettled) => void;
}

export function useConvertPoll(scriptId: string): ConvertPoll {
  const timersRef = useRef<Set<ReturnType<typeof setInterval>>>(new Set());

  // Stop any in-flight polls when the owning component unmounts.
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((timer) => clearInterval(timer));
      timers.clear();
    };
  }, []);

  const startPoll = useCallback<ConvertPoll['startPoll']>(
    (predicate, onSettled) => {
      let attempts = 0;
      const timers = timersRef.current;
      const timer = setInterval(async () => {
        attempts += 1;
        let data: PollData = { scenes: [], chapters: [] };
        let matched = false;
        try {
          const [scenes, project] = await Promise.all([
            listScenes(scriptId),
            fetchScriptProject(scriptId),
          ]);
          data = { scenes, chapters: project.chapters ?? [] };
          matched = predicate(data);
        } catch (err) {
          // A transient fetch failure still counts toward the cap so a persistent
          // error can't leak the interval or pin a caller on its busy state.
          console.error('[useConvertPoll] poll fetch failed', err);
        }
        if (matched || attempts >= CONVERT_POLL_MAX) {
          clearInterval(timer);
          timers.delete(timer);
          onSettled?.(matched, data);
        }
      }, CONVERT_POLL_MS);
      timers.add(timer);
    },
    [scriptId],
  );

  return { startPoll };
}
