/**
 * Pipelines manager (W2b) — team-scoped CRUD for content relay pipelines.
 *
 * A pipeline is a FIXED ordered relay of agent steps. Each step binds an agent
 * and two templates (title + prompt) rendered with {parent_title},
 * {parent_description}, {step_order}, {pipeline_name}, {prev_output}. Reachable
 * from the Issues page header; running a pipeline happens from an issue's detail
 * view (RunPipelineMenu).
 */

import React, { useEffect, useState } from 'react';
import { X, Plus, Trash2, ArrowUp, ArrowDown, GitBranch } from 'lucide-react';

import {
  createPipeline,
  deletePipeline,
  listPipelines,
  updatePipeline,
  type Pipeline,
  type PipelineStepInput,
} from '../../services/pipelinesService';
import type { AgentRef } from './types';
import { useToast } from '../Toast';

interface Props {
  teamId: string;
  agents: AgentRef[];
  onClose: () => void;
}

interface DraftStep extends PipelineStepInput {
  key: string; // stable react key while editing (not sent to backend)
}

interface Draft {
  id: string | null; // null = new
  name: string;
  description: string;
  enabled: boolean;
  steps: DraftStep[];
}

let _key = 0;
const nextKey = () => `s${_key++}`;

function emptyStep(order: number, agentId: string): DraftStep {
  return {
    key: nextKey(),
    step_order: order,
    agent_id: agentId,
    title_template: `Step ${order}: {parent_title}`,
    prompt_template: '{parent_description}\n\nPrevious step output:\n{prev_output}',
  };
}

function toDraft(p: Pipeline): Draft {
  return {
    id: p.id,
    name: p.name,
    description: p.description ?? '',
    enabled: p.enabled,
    steps: p.steps
      .slice()
      .sort((a, b) => a.step_order - b.step_order)
      .map((s) => ({
        key: nextKey(),
        step_order: s.step_order,
        agent_id: s.agent_id,
        title_template: s.title_template,
        prompt_template: s.prompt_template,
      })),
  };
}

export const PipelinesManagerModal: React.FC<Props> = ({ teamId, agents, onClose }) => {
  const { addToast } = useToast();
  const [pipelines, setPipelines] = useState<Pipeline[] | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);

  const reload = async () => {
    try {
      setPipelines(await listPipelines(teamId));
    } catch (err) {
      console.error('[PipelinesManager] load failed', err);
      addToast(err instanceof Error ? err.message : 'Failed to load pipelines', 'error');
      setPipelines([]);
    }
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamId]);

  const startNew = () => {
    const firstAgent = agents[0]?.id ?? '';
    setDraft({
      id: null,
      name: '',
      description: '',
      enabled: true,
      steps: [emptyStep(1, firstAgent)],
    });
  };

  const renumber = (steps: DraftStep[]): DraftStep[] =>
    steps.map((s, i) => ({ ...s, step_order: i + 1 }));

  const patchStep = (key: string, patch: Partial<DraftStep>) => {
    if (!draft) return;
    setDraft({
      ...draft,
      steps: draft.steps.map((s) => (s.key === key ? { ...s, ...patch } : s)),
    });
  };

  const moveStep = (idx: number, dir: -1 | 1) => {
    if (!draft) return;
    const next = draft.steps.slice();
    const j = idx + dir;
    if (j < 0 || j >= next.length) return;
    [next[idx], next[j]] = [next[j], next[idx]];
    setDraft({ ...draft, steps: renumber(next) });
  };

  const removeStep = (key: string) => {
    if (!draft) return;
    setDraft({ ...draft, steps: renumber(draft.steps.filter((s) => s.key !== key)) });
  };

  const addStep = () => {
    if (!draft) return;
    const firstAgent = agents[0]?.id ?? '';
    setDraft({
      ...draft,
      steps: [...draft.steps, emptyStep(draft.steps.length + 1, firstAgent)],
    });
  };

  const canSave =
    !!draft &&
    draft.name.trim().length > 0 &&
    draft.steps.length > 0 &&
    draft.steps.every((s) => s.agent_id && s.title_template.trim() && s.prompt_template.trim());

  const save = async () => {
    if (!draft || !canSave) return;
    setSaving(true);
    const steps: PipelineStepInput[] = draft.steps.map((s, i) => ({
      step_order: i + 1,
      agent_id: s.agent_id,
      title_template: s.title_template,
      prompt_template: s.prompt_template,
    }));
    try {
      if (draft.id) {
        await updatePipeline(draft.id, {
          name: draft.name.trim(),
          description: draft.description.trim() || null,
          enabled: draft.enabled,
          steps,
        });
        addToast('Pipeline updated', 'success');
      } else {
        await createPipeline({
          team_id: teamId,
          name: draft.name.trim(),
          description: draft.description.trim() || null,
          enabled: draft.enabled,
          steps,
        });
        addToast('Pipeline created', 'success');
      }
      setDraft(null);
      await reload();
    } catch (err) {
      console.error('[PipelinesManager] save failed', err);
      addToast(err instanceof Error ? err.message : 'Failed to save pipeline', 'error');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (p: Pipeline) => {
    try {
      await deletePipeline(p.id);
      addToast('Pipeline deleted', 'success');
      await reload();
    } catch (err) {
      console.error('[PipelinesManager] delete failed', err);
      addToast(err instanceof Error ? err.message : 'Failed to delete pipeline', 'error');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-lg border border-ink-800 bg-ink-950 shadow-2xl">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-ink-800">
          <GitBranch size={16} className="text-cyan-400" />
          <h2 className="text-[15px] font-semibold text-ink-100">
            {draft ? (draft.id ? 'Edit pipeline' : 'New pipeline') : 'Pipelines'}
          </h2>
          <button
            onClick={onClose}
            className="ml-auto p-1 text-ink-500 hover:text-ink-200 rounded hover:bg-ink-800"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {!draft ? (
            <PipelineList
              pipelines={pipelines}
              onNew={startNew}
              onEdit={(p) => setDraft(toDraft(p))}
              onDelete={remove}
            />
          ) : (
            <PipelineEditor
              draft={draft}
              agents={agents}
              onChange={setDraft}
              onPatchStep={patchStep}
              onMoveStep={moveStep}
              onRemoveStep={removeStep}
              onAddStep={addStep}
            />
          )}
        </div>

        {draft && (
          <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-ink-800">
            <button
              onClick={() => setDraft(null)}
              className="px-3 py-1.5 text-[13px] rounded border border-ink-800 text-ink-300 hover:bg-ink-800/60"
            >
              Cancel
            </button>
            <button
              onClick={save}
              disabled={!canSave || saving}
              className="px-3 py-1.5 text-[13px] rounded bg-cyan-600 text-white hover:bg-cyan-500 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saving ? 'Saving…' : 'Save pipeline'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

const PipelineList: React.FC<{
  pipelines: Pipeline[] | null;
  onNew: () => void;
  onEdit: (p: Pipeline) => void;
  onDelete: (p: Pipeline) => void;
}> = ({ pipelines, onNew, onEdit, onDelete }) => (
  <div className="space-y-3">
    <button
      onClick={onNew}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-[13px] rounded bg-cyan-600 text-white hover:bg-cyan-500"
    >
      <Plus size={14} /> New pipeline
    </button>
    {pipelines === null ? (
      <div className="text-[13px] text-ink-500 py-6 text-center">Loading…</div>
    ) : pipelines.length === 0 ? (
      <div className="text-[13px] text-ink-500 py-6 text-center border border-dashed border-ink-800 rounded">
        No pipelines yet. Create one to relay work across agents.
      </div>
    ) : (
      <ul className="space-y-1.5">
        {pipelines.map((p) => (
          <li
            key={p.id}
            className="flex items-center gap-3 px-3 py-2.5 rounded border border-ink-800 bg-ink-900/50"
          >
            <button
              onClick={() => onEdit(p)}
              className="flex-1 min-w-0 text-left"
            >
              <div className="flex items-center gap-2">
                <span className="text-[14px] text-ink-100 truncate">{p.name}</span>
                {!p.enabled && (
                  <span className="px-1.5 py-0.5 text-[11px] rounded bg-ink-800 text-ink-400">
                    Disabled
                  </span>
                )}
              </div>
              <div className="text-[12px] text-ink-500 mt-0.5">
                {p.steps.length} step{p.steps.length === 1 ? '' : 's'}
                {p.description ? ` · ${p.description}` : ''}
              </div>
            </button>
            <button
              onClick={() => onDelete(p)}
              className="p-1.5 text-ink-500 hover:text-rose-400 rounded hover:bg-ink-800"
              title="Delete pipeline"
            >
              <Trash2 size={14} />
            </button>
          </li>
        ))}
      </ul>
    )}
  </div>
);

const PipelineEditor: React.FC<{
  draft: Draft;
  agents: AgentRef[];
  onChange: (d: Draft) => void;
  onPatchStep: (key: string, patch: Partial<DraftStep>) => void;
  onMoveStep: (idx: number, dir: -1 | 1) => void;
  onRemoveStep: (key: string) => void;
  onAddStep: () => void;
}> = ({ draft, agents, onChange, onPatchStep, onMoveStep, onRemoveStep, onAddStep }) => (
  <div className="space-y-4">
    <div>
      <label className="block text-[12px] font-medium text-ink-400 mb-1">Name</label>
      <input
        value={draft.name}
        onChange={(e) => onChange({ ...draft, name: e.target.value })}
        placeholder="Content Relay"
        className="w-full px-3 py-2 text-[13px] rounded border border-ink-800 bg-ink-900 text-ink-100 focus:outline-none focus:border-cyan-500"
      />
    </div>
    <div>
      <label className="block text-[12px] font-medium text-ink-400 mb-1">
        Description <span className="text-ink-600">(optional)</span>
      </label>
      <input
        value={draft.description}
        onChange={(e) => onChange({ ...draft, description: e.target.value })}
        placeholder="Topic → Script → Storyboard → Final cut"
        className="w-full px-3 py-2 text-[13px] rounded border border-ink-800 bg-ink-900 text-ink-100 focus:outline-none focus:border-cyan-500"
      />
    </div>
    <label className="flex items-center gap-2 text-[13px] text-ink-300 cursor-pointer">
      <input
        type="checkbox"
        checked={draft.enabled}
        onChange={(e) => onChange({ ...draft, enabled: e.target.checked })}
        className="accent-cyan-500"
      />
      Enabled (available to run)
    </label>

    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[12px] font-medium text-ink-400 uppercase tracking-wider">
          Steps
        </span>
        <button
          onClick={onAddStep}
          className="inline-flex items-center gap-1 px-2 py-1 text-[12px] rounded border border-ink-800 text-ink-300 hover:bg-ink-800/60"
        >
          <Plus size={12} /> Add step
        </button>
      </div>
      <ol className="space-y-2">
        {draft.steps.map((s, idx) => (
          <li key={s.key} className="rounded border border-ink-800 bg-ink-900/40 p-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-cyan-500/15 text-cyan-300 text-[11px] font-mono">
                {idx + 1}
              </span>
              <select
                value={s.agent_id}
                onChange={(e) => onPatchStep(s.key, { agent_id: e.target.value })}
                className="flex-1 px-2 py-1.5 text-[13px] rounded border border-ink-800 bg-ink-900 text-ink-100 focus:outline-none focus:border-cyan-500"
              >
                {agents.length === 0 && <option value="">No agents available</option>}
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              <button
                onClick={() => onMoveStep(idx, -1)}
                disabled={idx === 0}
                className="p-1 text-ink-500 hover:text-ink-200 rounded hover:bg-ink-800 disabled:opacity-30"
                title="Move up"
              >
                <ArrowUp size={13} />
              </button>
              <button
                onClick={() => onMoveStep(idx, 1)}
                disabled={idx === draft.steps.length - 1}
                className="p-1 text-ink-500 hover:text-ink-200 rounded hover:bg-ink-800 disabled:opacity-30"
                title="Move down"
              >
                <ArrowDown size={13} />
              </button>
              <button
                onClick={() => onRemoveStep(s.key)}
                disabled={draft.steps.length === 1}
                className="p-1 text-ink-500 hover:text-rose-400 rounded hover:bg-ink-800 disabled:opacity-30"
                title="Remove step"
              >
                <Trash2 size={13} />
              </button>
            </div>
            <input
              value={s.title_template}
              onChange={(e) => onPatchStep(s.key, { title_template: e.target.value })}
              placeholder="Title template"
              className="w-full px-2 py-1.5 mb-1.5 text-[12px] rounded border border-ink-800 bg-ink-900 text-ink-200 focus:outline-none focus:border-cyan-500"
            />
            <textarea
              value={s.prompt_template}
              onChange={(e) => onPatchStep(s.key, { prompt_template: e.target.value })}
              placeholder="Prompt template — {parent_title}, {prev_output}, …"
              rows={2}
              className="w-full px-2 py-1.5 text-[12px] rounded border border-ink-800 bg-ink-900 text-ink-200 focus:outline-none focus:border-cyan-500 resize-y"
            />
          </li>
        ))}
      </ol>
      <p className="mt-2 text-[11px] text-ink-600">
        Template variables: {'{parent_title}'}, {'{parent_description}'}, {'{step_order}'},{' '}
        {'{pipeline_name}'}, {'{prev_output}'}.
      </p>
    </div>
  </div>
);
