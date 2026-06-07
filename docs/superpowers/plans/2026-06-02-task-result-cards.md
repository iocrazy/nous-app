# Typed Task Result Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Click a completed task in the Task Center → open a detail modal that renders the task's RESULT by type (media preview, agent LLM output + tokens, transcript text, summary + key points), mirroring the reference app's image/tts/llm/vision cards but adapted to mediahub's external-API data sources.

**Architecture:** A portal `TaskDetailModal` (Esc / backdrop close) opened from a `detailTaskId` state in `TaskCenter`. A pure `taskResultKind(task)` selector maps `task_type` → which body to render. Each typed body pulls from the source it needs: media from `resourceService.fetchResourceById` (+ reuse `AudioWaveformPlayer` for audio, link to `ResourceDetailPage` for video), transcript/summary from `aiService.get*ByResource`, agent LLM stats from the `agent_runs` row already streamed by `useAgentRunTasks` (extended to carry token/cost). Vision (`ai_extract`) is **out of scope** here — it has no read endpoint yet (only a trigger); see Prerequisites.

**Tech Stack:** React 19, TypeScript, Vite, vitest, i18next, TailwindCSS, lucide-react, `createPortal`.

---

## Prerequisites / Out of scope

- **`ai_extract` (vision) is deferred.** `aiService.ts` has `triggerVisualAnalysisByResource` but **no `getVisualAnalysisByResource`** read fn, and no analysis result type. Rendering a vision card needs that backend binding first (separate small task: add `GET /api/v1/ai/analysis/resource/{id}` + `aiService.getVisualAnalysisByResource` + a result type). Until then `ai_extract` falls through to the generic body. This plan does NOT build the vision card.
- This plan does NOT embed a video player in the modal — mediahub already has `ResourceDetailPage` for full playback; the media body previews + links there. Audio DOES embed `AudioWaveformPlayer` (high value, component exists, no separate need).

## File Structure

- `frontend/components/TaskCenter/taskResultKind.ts` (new) — pure `taskResultKind(task) -> 'media' | 'agent' | 'transcript' | 'summary' | 'generic'`. Tested.
- `frontend/components/TaskCenter/useTaskResult.ts` (new) — hook: given a task + its result kind, fetch the typed payload (resource / transcript / summary); agent + generic need no fetch. Returns `{ loading, error, data }`.
- `frontend/components/TaskCenter/TaskDetailModal.tsx` (new) — portal shell + header + body dispatcher + generic fallback.
- `frontend/components/TaskCenter/bodies/MediaResultBody.tsx`, `AgentResultBody.tsx`, `TextResultBody.tsx` (new) — typed bodies.
- `frontend/components/TaskCenter/useAgentRunTasks.ts` (modify) — extend SELECT + carry token/cost/model into the mapped task's `metadata`.
- `frontend/components/TaskCenter/agentRunPresentation.ts` (modify) + `.test.ts` — stash agent stats in `metadata`.
- `frontend/components/TopBar.tsx` (modify) — `TaskCenterPanel` owns `detailTaskId`; a completed `TaskCenterRow` click opens the modal instead of navigating.
- `frontend/components/TaskCenter/TaskCenterRow.tsx` (modify) — row click calls `onOpenDetail(task)` for terminal tasks (keep Open/Download quick actions).
- `frontend/public/locales/{en,zh}.json` — modal labels.

---

### Task 1: `taskResultKind` selector (pure)

**Files:**
- Create: `frontend/components/TaskCenter/taskResultKind.ts`
- Test: `frontend/components/TaskCenter/taskResultKind.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from 'vitest';
import { taskResultKind } from './taskResultKind';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

const t = (over: Partial<UnifiedTask>): UnifiedTask =>
  ({ id: '1', user_id: 'u', task_type: 'download', status: 'completed', title: 'x', progress: 100, metadata: {}, created_at: '', ...over } as UnifiedTask);

describe('taskResultKind', () => {
  it('media for download/parse/upload/transcode with a resource', () => {
    for (const tt of ['download', 'parse', 'upload', 'transcode'] as const) {
      expect(taskResultKind(t({ task_type: tt, resource_id: 'r1' }))).toBe('media');
    }
  });
  it('agent for agent runs', () => {
    expect(taskResultKind(t({ task_type: 'agent' }))).toBe('agent');
  });
  it('transcript / summary for their ai types', () => {
    expect(taskResultKind(t({ task_type: 'ai_transcription', resource_id: 'r' }))).toBe('transcript');
    expect(taskResultKind(t({ task_type: 'ai_summary', resource_id: 'r' }))).toBe('summary');
  });
  it('generic for vision (ai_extract) — no read endpoint yet', () => {
    expect(taskResultKind(t({ task_type: 'ai_extract', resource_id: 'r' }))).toBe('generic');
  });
  it('generic for media tasks with no resource (e.g. failed)', () => {
    expect(taskResultKind(t({ task_type: 'download', resource_id: undefined }))).toBe('generic');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/TaskCenter/taskResultKind.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

export type ResultKind = 'media' | 'agent' | 'transcript' | 'summary' | 'generic';

/**
 * Which detail body renders a task's result. Media types need a resource to
 * preview; ai_extract (vision) has no read endpoint yet so it falls through to
 * generic (see the plan's Prerequisites).
 */
export function taskResultKind(task: Pick<UnifiedTask, 'task_type' | 'resource_id'>): ResultKind {
  switch (task.task_type) {
    case 'download':
    case 'parse':
    case 'upload':
    case 'transcode':
      return task.resource_id ? 'media' : 'generic';
    case 'agent':
      return 'agent';
    case 'ai_transcription':
      return task.resource_id ? 'transcript' : 'generic';
    case 'ai_summary':
      return task.resource_id ? 'summary' : 'generic';
    default:
      return 'generic';
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/TaskCenter/taskResultKind.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/TaskCenter/taskResultKind.ts frontend/components/TaskCenter/taskResultKind.test.ts
git commit -m "feat(tasks): taskResultKind selector for typed result cards"
```

---

### Task 2: Carry agent token/cost through to the mapped task

**Files:**
- Modify: `frontend/components/TaskCenter/agentRunPresentation.ts`
- Modify: `frontend/components/TaskCenter/agentRunPresentation.test.ts`
- Modify: `frontend/components/TaskCenter/useAgentRunTasks.ts` (extend SELECT)

- [ ] **Step 1: Add a failing test for the carried stats**

Append to `agentRunPresentation.test.ts`:

```typescript
describe('agentRunToTask — LLM stats in metadata', () => {
  it('carries tokens / cost / model into metadata for the agent card', () => {
    const task = agentRunToTask(
      baseRow({
        status: 'completed',
        output_summary: 'done',
        prompt_tokens: 234,
        completion_tokens: 56,
        cost_cents: 1.5,
        model: 'qwen3-32b',
      } as any),
    );
    expect(task.metadata).toMatchObject({
      agent_prompt_tokens: 234,
      agent_completion_tokens: 56,
      agent_cost_cents: 1.5,
      agent_model: 'qwen3-32b',
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/TaskCenter/agentRunPresentation.test.ts`
Expected: FAIL — metadata is `{}` (stats not carried).

- [ ] **Step 3: Extend the row type + mapping**

In `agentRunPresentation.ts`, add the optional fields to `AgentRunRow` and populate `metadata`:

```typescript
export interface AgentRunRow {
  id: string;
  user_id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'heartbeat_lost';
  trigger: string;
  input_summary: string | null;
  output_summary: string | null;
  error_message: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  cost_cents?: number | null;
  model?: string | null;
}
```

In `agentRunToTask`, replace `metadata: {},` with:

```typescript
    metadata: {
      agent_prompt_tokens: run.prompt_tokens ?? null,
      agent_completion_tokens: run.completion_tokens ?? null,
      agent_cost_cents: run.cost_cents ?? null,
      agent_model: run.model ?? null,
      agent_output: run.output_summary ?? null,
      agent_input: run.input_summary ?? null,
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/TaskCenter/agentRunPresentation.test.ts`
Expected: PASS.

- [ ] **Step 5: Extend the realtime SELECT to fetch those columns**

In `useAgentRunTasks.ts`, change the `SELECT` constant:

```typescript
const SELECT =
  'id,user_id,status,trigger,input_summary,output_summary,error_message,started_at,ended_at,created_at,prompt_tokens,completion_tokens,cost_cents,model';
```

- [ ] **Step 6: Run the TaskCenter suite to confirm no regression**

Run: `cd frontend && npx vitest run components/TaskCenter/`
Expected: PASS (existing + new).

- [ ] **Step 7: Commit**

```bash
git add frontend/components/TaskCenter/agentRunPresentation.ts frontend/components/TaskCenter/agentRunPresentation.test.ts frontend/components/TaskCenter/useAgentRunTasks.ts
git commit -m "feat(tasks): carry agent token/cost/model into the mapped task for the result card"
```

---

### Task 3: `useTaskResult` hook (fetch typed payload)

**Files:**
- Create: `frontend/components/TaskCenter/useTaskResult.ts`

This hook has little pure logic to unit-test (it's fetch orchestration); it is exercised through the bodies. Keep it small and defensive.

- [ ] **Step 1: Implement**

```typescript
import { useEffect, useState } from 'react';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { taskResultKind, type ResultKind } from './taskResultKind';
import { fetchResourceById } from '../../services/resourceService';
import { getTranscriptByResource, getSummaryByResource } from '../../services/aiService';

export interface TaskResultState {
  kind: ResultKind;
  loading: boolean;
  error: string | null;
  /** resource (media), transcript, or summary payload — shape depends on kind. */
  data: unknown;
}

export function useTaskResult(task: UnifiedTask | null): TaskResultState {
  const kind = task ? taskResultKind(task) : 'generic';
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: unknown }>({
    loading: false,
    error: null,
    data: null,
  });

  useEffect(() => {
    if (!task) return;
    // agent + generic render from the task itself — no fetch.
    if (kind === 'agent' || kind === 'generic' || !task.resource_id) {
      setState({ loading: false, error: null, data: null });
      return;
    }
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    const rid = String(task.resource_id);
    const run = async () => {
      try {
        let data: unknown = null;
        if (kind === 'media') data = await fetchResourceById(rid);
        else if (kind === 'transcript') data = await getTranscriptByResource(rid);
        else if (kind === 'summary') data = await getSummaryByResource(rid);
        if (!cancelled) setState({ loading: false, error: null, data });
      } catch (err) {
        if (!cancelled) {
          setState({
            loading: false,
            error: err instanceof Error ? err.message : 'Failed to load result',
            data: null,
          });
        }
      }
    };
    run();
    return () => { cancelled = true; };
  }, [task, kind]);

  return { kind, ...state };
}
```

> Verify the exact exported names/signatures before running: `fetchResourceById` (`services/resourceService.ts:41`), `getTranscriptByResource` / `getSummaryByResource` (`services/aiService.ts`). If a name differs (e.g. `getTranscriptByResource` is actually `getTranscriptByResourceId`), match it.

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -E "useTaskResult|aiService|resourceService" | head`
Expected: no errors referencing the new file (imports resolve).

- [ ] **Step 3: Commit**

```bash
git add frontend/components/TaskCenter/useTaskResult.ts
git commit -m "feat(tasks): useTaskResult hook fetches typed result payload"
```

---

### Task 4: `TaskDetailModal` shell + dispatcher + GenericBody

**Files:**
- Create: `frontend/components/TaskCenter/TaskDetailModal.tsx`

- [ ] **Step 1: Implement the modal shell**

```tsx
import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { taskTypeLabel } from '../../contexts/TaskManagerContext';
import { useTaskResult } from './useTaskResult';
import { MediaResultBody } from './bodies/MediaResultBody';
import { AgentResultBody } from './bodies/AgentResultBody';
import { TextResultBody } from './bodies/TextResultBody';

interface TaskDetailModalProps {
  task: UnifiedTask | null;
  onClose: () => void;
  onOpenResource: (resourceId: string) => void;
}

export const TaskDetailModal: React.FC<TaskDetailModalProps> = ({ task, onClose, onOpenResource }) => {
  const { t } = useTranslation();
  const result = useTaskResult(task);

  useEffect(() => {
    if (!task) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [task, onClose]);

  if (!task) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl max-h-[80vh] flex flex-col bg-zinc-900 border border-zinc-700/60 rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2.5 px-4 h-13 py-3 border-b border-zinc-800 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-zinc-100 truncate">{task.title}</div>
            <div className="text-[11px] text-zinc-500">
              {taskTypeLabel(task.task_type)} · {task.status}
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors" aria-label={t('common.close')}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {result.loading && (
            <div className="flex items-center justify-center py-12 text-zinc-500">
              <div className="w-5 h-5 border-2 border-zinc-600 border-t-indigo-400 rounded-full animate-spin" />
            </div>
          )}
          {!result.loading && result.error && (
            <div className="p-4 text-xs text-red-400">{result.error}</div>
          )}
          {!result.loading && !result.error && (
            <>
              {result.kind === 'media' && (
                <MediaResultBody task={task} resource={result.data} onOpenResource={onOpenResource} />
              )}
              {result.kind === 'agent' && <AgentResultBody task={task} />}
              {(result.kind === 'transcript' || result.kind === 'summary') && (
                <TextResultBody kind={result.kind} data={result.data} />
              )}
              {result.kind === 'generic' && <GenericResultBody task={task} />}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
};

const GenericResultBody: React.FC<{ task: UnifiedTask }> = ({ task }) => {
  const { t } = useTranslation();
  const hasMeta = task.metadata && Object.keys(task.metadata).length > 0;
  return (
    <div className="p-4 space-y-3">
      {task.error_msg && (
        <div className="text-xs text-red-400 whitespace-pre-wrap break-words">{task.error_msg}</div>
      )}
      {hasMeta ? (
        <pre className="text-[11px] text-zinc-400 font-mono whitespace-pre-wrap break-words bg-zinc-950/50 rounded p-3">
          {JSON.stringify(task.metadata, null, 2)}
        </pre>
      ) : (
        <div className="text-xs text-zinc-500">{t('topbar.noResultDetail')}</div>
      )}
    </div>
  );
};
```

- [ ] **Step 2: Type-check (will error until bodies exist — expected)**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep "TaskDetailModal" | head`
Expected: errors about missing `./bodies/*` modules — resolved in Tasks 5–6. (Do not commit yet.)

- [ ] **Step 3: Defer commit until bodies exist (Task 6 Step 5 commits the modal + bodies together).**

---

### Task 5: `MediaResultBody` + `TextResultBody`

**Files:**
- Create: `frontend/components/TaskCenter/bodies/MediaResultBody.tsx`
- Create: `frontend/components/TaskCenter/bodies/TextResultBody.tsx`

- [ ] **Step 1: MediaResultBody**

Reuse `getResourceCoverUrl` (cover) + `AudioWaveformPlayer` for audio; everything else previews cover + links to the library detail page.

```tsx
import React from 'react';
import { ExternalLink, Download } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UnifiedTask } from '../../../contexts/TaskManagerContext';
import { getResourceCoverUrl, getResourceFileUrl, getResourceMediaUrl } from '../../../services/resourceService';
import { AudioWaveformPlayer } from '../../AudioWaveformPlayer';

interface MediaResultBodyProps {
  task: UnifiedTask;
  resource: unknown; // Resource shape from fetchResourceById
  onOpenResource: (resourceId: string) => void;
}

export const MediaResultBody: React.FC<MediaResultBodyProps> = ({ task, resource, onOpenResource }) => {
  const { t } = useTranslation();
  const rid = String(task.resource_id);
  const r = (resource ?? {}) as {
    filename?: string; mime_type?: string | null; duration_seconds?: number | null;
    resolution?: string | null; file_size_bytes?: number | null;
  };
  const isAudio = (r.mime_type ?? '').startsWith('audio/');

  return (
    <div className="p-4 space-y-4">
      {isAudio ? (
        <AudioWaveformPlayer src={getResourceMediaUrl(rid)} coverUrl={getResourceCoverUrl(rid)} />
      ) : (
        <img src={getResourceCoverUrl(rid)} alt="" className="w-full max-h-72 object-contain rounded-lg bg-zinc-950" />
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        {r.filename && <Row label={t('topbar.resFilename')} value={r.filename} />}
        {r.resolution && <Row label={t('topbar.resResolution')} value={r.resolution} />}
        {r.duration_seconds != null && <Row label={t('topbar.resDuration')} value={`${Math.round(r.duration_seconds)}s`} />}
        {r.file_size_bytes != null && <Row label={t('topbar.resSize')} value={`${(r.file_size_bytes / 1e6).toFixed(1)} MB`} />}
      </dl>

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={() => onOpenResource(rid)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs text-indigo-300 bg-indigo-500/15 hover:bg-indigo-500/25 transition-colors"
        >
          <ExternalLink size={13} /> {t('topbar.openResource')}
        </button>
        <a
          href={getResourceFileUrl(rid)}
          download
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs text-zinc-300 bg-zinc-800 hover:bg-zinc-700 transition-colors"
        >
          <Download size={13} /> {t('topbar.downloadResult')}
        </a>
      </div>
    </div>
  );
};

const Row: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="flex flex-col">
    <dt className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</dt>
    <dd className="text-zinc-200 truncate">{value}</dd>
  </div>
);
```

> Verify `AudioWaveformPlayer`'s real props (`src`, `coverUrl`, `theme?`) in `components/AudioWaveformPlayer.tsx` and `getResourceMediaUrl` exists (`resourceService.ts:47`). Adjust prop names to match.

- [ ] **Step 2: TextResultBody (transcript + summary)**

```tsx
import React from 'react';
import { useTranslation } from 'react-i18next';

interface TextResultBodyProps {
  kind: 'transcript' | 'summary';
  data: unknown;
}

export const TextResultBody: React.FC<TextResultBodyProps> = ({ kind, data }) => {
  const { t } = useTranslation();
  const d = (data ?? {}) as {
    text?: string; summary?: string; key_points?: string[]; topics?: string[];
  };
  const body = kind === 'transcript' ? d.text : d.summary;

  return (
    <div className="p-4 space-y-3">
      {body ? (
        <div className="text-[13px] leading-relaxed text-zinc-200 whitespace-pre-wrap break-words bg-zinc-950/40 rounded p-3 border border-zinc-800">
          {body}
        </div>
      ) : (
        <div className="text-xs text-zinc-500">{t('topbar.noResultDetail')}</div>
      )}

      {kind === 'summary' && Array.isArray(d.key_points) && d.key_points.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1">{t('topbar.keyPoints')}</div>
          <ul className="list-disc list-inside space-y-1 text-xs text-zinc-300">
            {d.key_points.map((kp, i) => <li key={i}>{kp}</li>)}
          </ul>
        </div>
      )}

      {kind === 'summary' && Array.isArray(d.topics) && d.topics.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {d.topics.map((tp, i) => (
            <span key={i} className="px-2 py-0.5 rounded-full text-[10px] bg-zinc-800 text-zinc-300">{tp}</span>
          ))}
        </div>
      )}
    </div>
  );
};
```

> Verify the transcript/summary response shapes from `aiService.ts` (the agent reported transcript → `{text, segments, language, duration}`, summary → `{summary, key_points, topics}`). Match field names exactly.

- [ ] **Step 3: Type-check both bodies**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -E "MediaResultBody|TextResultBody" | head`
Expected: no errors in these files.

---

### Task 6: `AgentResultBody` + wire the modal commit

**Files:**
- Create: `frontend/components/TaskCenter/bodies/AgentResultBody.tsx`

- [ ] **Step 1: AgentResultBody (LLM input/output + token/cost stats)**

```tsx
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { UnifiedTask } from '../../../contexts/TaskManagerContext';

export const AgentResultBody: React.FC<{ task: UnifiedTask }> = ({ task }) => {
  const { t } = useTranslation();
  const m = task.metadata as {
    agent_input?: string | null; agent_output?: string | null;
    agent_prompt_tokens?: number | null; agent_completion_tokens?: number | null;
    agent_cost_cents?: number | null; agent_model?: string | null;
  };
  const total = (m.agent_prompt_tokens ?? 0) + (m.agent_completion_tokens ?? 0);

  return (
    <div className="p-4 space-y-3">
      {m.agent_input && (
        <Block label={t('topbar.agentPrompt')} text={m.agent_input} tone="plain" />
      )}
      <Block label={t('topbar.agentResponse')} text={m.agent_output || task.error_msg || ''} tone="accent" />

      <div className="flex flex-wrap gap-4 pt-1">
        {m.agent_model && <Stat label={t('topbar.agentModel')} value={m.agent_model} />}
        {m.agent_prompt_tokens != null && <Stat label="prompt" value={`${m.agent_prompt_tokens}`} unit="tok" />}
        {m.agent_completion_tokens != null && <Stat label="completion" value={`${m.agent_completion_tokens}`} unit="tok" />}
        {total > 0 && <Stat label="total" value={`${total}`} unit="tok" />}
        {m.agent_cost_cents != null && <Stat label="cost" value={`${m.agent_cost_cents}`} unit="¢" />}
      </div>
    </div>
  );
};

const Block: React.FC<{ label: string; text: string; tone: 'plain' | 'accent' }> = ({ label, text, tone }) => (
  <div>
    <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1">{label}</div>
    <div className={`text-[13px] leading-relaxed whitespace-pre-wrap break-words rounded p-3 ${tone === 'accent' ? 'text-zinc-100 bg-indigo-500/10 border-l-2 border-indigo-400' : 'text-zinc-300 bg-zinc-950/40 border border-zinc-800'}`}>
      {text || <span className="text-zinc-600">—</span>}
    </div>
  </div>
);

const Stat: React.FC<{ label: string; value: string; unit?: string }> = ({ label, value, unit }) => (
  <div className="flex flex-col">
    <span className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</span>
    <span className="text-sm font-semibold text-zinc-100">{value}{unit && <span className="ml-0.5 text-[11px] text-zinc-500">{unit}</span>}</span>
  </div>
);
```

- [ ] **Step 2: Add the i18n keys**

In `frontend/public/locales/en.json` under `topbar`, add: `"noResultDetail": "No result detail"`, `"keyPoints": "Key points"`, `"resFilename": "File"`, `"resResolution": "Resolution"`, `"resDuration": "Duration"`, `"resSize": "Size"`, `"agentPrompt": "Prompt"`, `"agentResponse": "Response"`, `"agentModel": "Model"`. Add `"close": "Close"` under `common` if missing.
In `zh.json` mirror with: `"noResultDetail": "暂无结果详情"`, `"keyPoints": "要点"`, `"resFilename": "文件"`, `"resResolution": "分辨率"`, `"resDuration": "时长"`, `"resSize": "大小"`, `"agentPrompt": "输入"`, `"agentResponse": "回复"`, `"agentModel": "模型"`, `common.close": "关闭"`.

- [ ] **Step 3: Type-check the whole modal subtree**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep "TaskCenter/" | head`
Expected: no errors in the TaskCenter modal files.

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build 2>&1 | tail -3`
Expected: build succeeds.

- [ ] **Step 5: Commit the modal + all bodies**

```bash
git add frontend/components/TaskCenter/TaskDetailModal.tsx frontend/components/TaskCenter/bodies/ frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(tasks): TaskDetailModal with typed result bodies (media/agent/transcript/summary)"
```

---

### Task 7: Wire the modal into the Task Center (row click → detail)

**Files:**
- Modify: `frontend/components/TopBar.tsx` (`TaskCenterPanel`)
- Modify: `frontend/components/TaskCenter/TaskCenterRow.tsx`

- [ ] **Step 1: Add an `onOpenDetail` prop to `TaskCenterRow`; terminal-row click opens detail**

In `TaskCenterRow.tsx`, add to the props interface: `onOpenDetail: (task: UnifiedTask) => void;`. Change the row's `onClick`: a completed/failed/cancelled row opens the detail modal; an active row stays non-clickable. Replace the existing `onClick={actions.open ? handleOpen : undefined}` on the outer row `div` with:

```tsx
      onClick={!isActive ? () => onOpenDetail(task) : undefined}
```

and make the outer `div` show the pointer cursor when `!isActive`. Keep the hover Open/Download buttons and their `e.stopPropagation()` so they don't double-trigger.

> The quick "Open" action still navigates to the library; the row click now opens the in-panel result modal. Both are intentional (modal = preview, Open = full page).

- [ ] **Step 2: Own `detailTaskId` in `TaskCenterPanel` + render the modal**

In `TopBar.tsx`, import `TaskDetailModal` and (already imported) `UnifiedTask`. In `TaskCenterPanel`, add state `const [detailTask, setDetailTask] = useState<UnifiedTask | null>(null);`. Pass `onOpenDetail={setDetailTask}` to every `<TaskCenterRow .../>`. Render `<TaskDetailModal task={detailTask} onClose={() => setDetailTask(null)} onOpenResource={openResource} />` just before the closing `</PanelShell>`.

- [ ] **Step 3: Full type-check + tests + build**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -c "error TS"` (expect baseline unchanged)
Run: `cd frontend && npx vitest run` (expect all pass)
Run: `cd frontend && npm run build 2>&1 | tail -3` (expect success)

- [ ] **Step 4: Commit**

```bash
git add frontend/components/TopBar.tsx frontend/components/TaskCenter/TaskCenterRow.tsx
git commit -m "feat(tasks): open the typed result modal on completed-task click"
```

---

## Deploy & Verify (after merge)

Frontend-only → Vercel per-PR preview. Verify:
- Click a completed **download/audio** task → modal shows cover (audio: waveform player) + metadata + Open/Download.
- Click a completed **agent** (chat/issue) task → modal shows input/response + token/cost/model stats.
- Click a completed **transcription** task → transcript text; **summary** task → summary + key points + topics.
- A **vision (ai_extract)** or resource-less task → generic body (metadata/JSON), no crash.
- Esc / backdrop closes the modal; active tasks aren't clickable.

---

## Self-Review

**1. Spec coverage** — typed cards for media / agent / transcript / summary (Tasks 4–7), backed by `taskResultKind` (Task 1) + agent stats carry-through (Task 2) + fetch hook (Task 3). Vision explicitly deferred with a stated prerequisite. ✓

**2. Placeholder scan** — every code step has full component code; the three "verify the real export name/props" notes (resourceService / aiService / AudioWaveformPlayer) are pointed checks against named files, not vague TODOs. ✓

**3. Type consistency** — `ResultKind` union is identical in `taskResultKind.ts`, `useTaskResult.ts`, and the modal dispatcher. Agent metadata keys (`agent_prompt_tokens` / `agent_completion_tokens` / `agent_cost_cents` / `agent_model` / `agent_input` / `agent_output`) are written in Task 2 and read verbatim in Task 6's `AgentResultBody`. `onOpenResource` (modal → library nav) reuses the existing `openResource` in `TaskCenterPanel`. ✓

**Known follow-ups (not in this plan):** vision read endpoint + card; richer transcript segment scrubber; result thumbnail caching. Logged here so they aren't mistaken for gaps.
