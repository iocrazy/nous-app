/**
 * Team workflow template editor (Project Workflow M1, PR-C / spec §4).
 *
 * Feishu-style orchestrator: a left rail of the team's templates, a middle
 * capsule chain (drag to reorder, parallel groups drawn as dashed vertical
 * boxes, "+ Add from library" to append node-bank stages) and a right
 * inspector with three tabs — Node Info (editable), Flow Rules and Events
 * (M1 fixed-convention, read-only per spec).
 *
 * The chain edits a local draft; Save writes the whole node list back via
 * PATCH /workflows/{id} (full replacement). Owner/members go through the
 * shared OwnerPicker; the candidate pools (team members + agents) are loaded
 * once and handed down.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  GitBranch,
  Plus,
  Star,
  Trash2,
} from 'lucide-react';
import { useToast } from '../Toast';
import { aiLibraryService } from '../../services/aiLibraryService';
import { fetchTeamMembers } from '../../services/teamService';
import {
  createTemplate,
  deleteTemplate,
  fetchStageLibrary,
  fetchTemplate,
  fetchTemplates,
  updateTemplate,
} from '../../services/workflowService';
import type {
  StageLibraryItem,
  WorkflowMemberRef,
  WorkflowTemplate,
  WorkflowTemplateNodeInput,
} from '../../types';
import { AgentOption, OwnerPicker, PersonOption } from './OwnerPicker';
import { LibraryPickerModal } from './LibraryPickerModal';

interface WorkflowTemplateEditorProps {
  teamId: string;
}

/** Editable node draft — carries a stable local key for reorder + selection. */
interface DraftNode extends WorkflowTemplateNodeInput {
  _key: string;
  parallel_group: number | null;
  skip_default: boolean;
  review_required: boolean;
  deliverable_required: boolean;
  members: WorkflowMemberRef[];
}

let _keySeq = 0;
const nextKey = () => `n${Date.now()}_${_keySeq++}`;

function toDraft(nodes: WorkflowTemplate['nodes']): DraftNode[] {
  return (nodes ?? []).map((n) => ({
    _key: n.id,
    name: n.name,
    sort_order: n.sort_order,
    parallel_group: n.parallel_group ?? null,
    default_owner_user_id: n.default_owner_user_id,
    default_owner_agent_id: n.default_owner_agent_id,
    skip_default: n.skip_default,
    review_required: n.review_required,
    deliverable_required: n.deliverable_required,
    deliverable_label: n.deliverable_label,
    source_stage_id: n.source_stage_id,
    duration_days: n.duration_days,
    members: n.members ?? [],
  }));
}

function toPayload(drafts: DraftNode[]): WorkflowTemplateNodeInput[] {
  return drafts.map((d, i) => ({
    name: d.name,
    sort_order: i,
    parallel_group: d.parallel_group,
    default_owner_user_id: d.default_owner_user_id ?? null,
    default_owner_agent_id: d.default_owner_agent_id ?? null,
    skip_default: d.skip_default,
    review_required: d.review_required,
    deliverable_required: d.deliverable_required,
    deliverable_label: d.deliverable_label ?? null,
    source_stage_id: d.source_stage_id ?? null,
    duration_days: d.duration_days ?? null,
    members: d.members,
  }));
}

/** Group consecutive drafts sharing a non-null parallel_group into runs. */
function groupRuns(
  drafts: DraftNode[],
): { group: number | null; items: DraftNode[] }[] {
  const runs: { group: number | null; items: DraftNode[] }[] = [];
  for (const d of drafts) {
    const last = runs[runs.length - 1];
    if (d.parallel_group != null && last && last.group === d.parallel_group) {
      last.items.push(d);
    } else {
      runs.push({ group: d.parallel_group, items: [d] });
    }
  }
  return runs;
}

export const WorkflowTemplateEditor: React.FC<WorkflowTemplateEditorProps> = ({
  teamId,
}) => {
  const { addToast } = useToast();
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<WorkflowTemplate | null>(null);
  const [drafts, setDrafts] = useState<DraftNode[]>([]);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [inspectorTab, setInspectorTab] = useState<'info' | 'flow' | 'events'>('info');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [library, setLibrary] = useState<StageLibraryItem[]>([]);
  const [people, setPeople] = useState<PersonOption[]>([]);
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [dragKey, setDragKey] = useState<string | null>(null);

  // ── candidate pools + node bank (once) ─────────────────────────────────
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [mem, ag, lib] = await Promise.all([
          fetchTeamMembers(teamId).catch(() => []),
          aiLibraryService.listAgents().catch(() => []),
          fetchStageLibrary().catch(() => []),
        ]);
        if (!alive) return;
        setPeople(mem.map((m) => ({ id: m.user_id, name: m.name || m.email || 'Member' })));
        setAgents(ag.map((a) => ({ id: a.id, name: a.name, slug: a.slug })));
        setLibrary(lib);
      } catch (err) {
        console.error('[WorkflowTemplateEditor] pools load failed', err);
      }
    })();
    return () => {
      alive = false;
    };
  }, [teamId]);

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchTemplates(teamId);
      setTemplates(list);
      setSelectedId((cur) => cur ?? list.find((t) => t.is_default)?.id ?? list[0]?.id ?? null);
    } catch (err) {
      console.error('[WorkflowTemplateEditor] load templates failed', err);
      addToast('Failed to load workflow templates', 'error');
    } finally {
      setLoading(false);
    }
  }, [teamId, addToast]);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  // ── selected template detail ───────────────────────────────────────────
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDrafts([]);
      return;
    }
    let alive = true;
    (async () => {
      try {
        const d = await fetchTemplate(selectedId);
        if (!alive) return;
        setDetail(d);
        setDrafts(toDraft(d.nodes));
        setSelectedNodeKey(d.nodes?.[0]?.id ?? null);
        setDirty(false);
      } catch (err) {
        console.error('[WorkflowTemplateEditor] load detail failed', err);
        addToast('Failed to load template', 'error');
      }
    })();
    return () => {
      alive = false;
    };
  }, [selectedId, addToast]);

  const selectedNode = useMemo(
    () => drafts.find((d) => d._key === selectedNodeKey) ?? null,
    [drafts, selectedNodeKey],
  );

  const patchNode = (key: string, patch: Partial<DraftNode>) => {
    setDrafts((prev) => prev.map((d) => (d._key === key ? { ...d, ...patch } : d)));
    setDirty(true);
  };

  const removeNode = (key: string) => {
    setDrafts((prev) => prev.filter((d) => d._key !== key));
    if (selectedNodeKey === key) setSelectedNodeKey(null);
    setDirty(true);
  };

  const addFromLibrary = (item: StageLibraryItem) => {
    const draft: DraftNode = {
      _key: nextKey(),
      name: item.name,
      sort_order: drafts.length,
      parallel_group: null,
      default_owner_user_id: null,
      default_owner_agent_id: null,
      skip_default: false,
      review_required: item.review_required,
      deliverable_required: false,
      deliverable_label: item.deliverable_label,
      source_stage_id: item.id,
      duration_days: null,
      members: [],
    };
    setDrafts((prev) => [...prev, draft]);
    setSelectedNodeKey(draft._key);
    setDirty(true);
    setLibraryOpen(false);
  };

  const onDrop = (targetKey: string) => {
    if (!dragKey || dragKey === targetKey) return;
    setDrafts((prev) => {
      const from = prev.findIndex((d) => d._key === dragKey);
      const to = prev.findIndex((d) => d._key === targetKey);
      if (from < 0 || to < 0) return prev;
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
    setDragKey(null);
    setDirty(true);
  };

  const save = async () => {
    if (!selectedId) return;
    setSaving(true);
    try {
      const updated = await updateTemplate(selectedId, { nodes: toPayload(drafts) });
      setDetail(updated);
      setDrafts(toDraft(updated.nodes));
      setDirty(false);
      addToast('Template saved', 'success');
      void loadTemplates();
    } catch (err) {
      console.error('[WorkflowTemplateEditor] save failed', err);
      addToast('Failed to save template', 'error');
    } finally {
      setSaving(false);
    }
  };

  const onNewTemplate = async () => {
    try {
      const tpl = await createTemplate(teamId, 'New Template');
      await loadTemplates();
      setSelectedId(tpl.id);
      addToast('Template created', 'success');
    } catch (err) {
      console.error('[WorkflowTemplateEditor] create failed', err);
      addToast('Failed to create template', 'error');
    }
  };

  const onSetDefault = async () => {
    if (!selectedId) return;
    try {
      await updateTemplate(selectedId, { is_default: true });
      await loadTemplates();
      addToast('Set as default', 'success');
    } catch (err) {
      console.error('[WorkflowTemplateEditor] set default failed', err);
      addToast('Failed to set default', 'error');
    }
  };

  const onDelete = async () => {
    if (!selectedId) return;
    try {
      await deleteTemplate(selectedId);
      setSelectedId(null);
      await loadTemplates();
      addToast('Template deleted', 'success');
    } catch (err) {
      console.error('[WorkflowTemplateEditor] delete failed', err);
      addToast('Failed to delete template', 'error');
    }
  };

  const runs = useMemo(() => groupRuns(drafts), [drafts]);

  return (
    <div className="flex h-full min-h-0 gap-4" data-testid="workflow-template-editor">
      {/* Templates rail */}
      <aside className="flex w-48 shrink-0 flex-col border-r border-line pr-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[11px] font-medium uppercase tracking-wider text-ink-600">
            Templates
          </span>
          <button
            onClick={onNewTemplate}
            className="rounded p-1 text-ink-400 hover:text-ink-200"
            title="New template"
            data-testid="workflow-new-template"
          >
            <Plus size={14} />
          </button>
        </div>
        <div className="flex flex-col gap-0.5 overflow-y-auto">
          {templates.map((t) => (
            <button
              key={t.id}
              onClick={() => setSelectedId(t.id)}
              className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] transition ${
                selectedId === t.id
                  ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                  : 'text-ink-400 hover:bg-ink-800/40 hover:text-ink-200'
              }`}
            >
              <GitBranch size={14} className="shrink-0" />
              <span className="flex-1 truncate text-left">{t.name}</span>
              {t.is_default && <Star size={12} className="shrink-0 fill-current text-amber-400" />}
            </button>
          ))}
          {templates.length === 0 && !loading && (
            <span className="px-2 py-2 text-[12px] text-ink-600">No templates yet</span>
          )}
        </div>
      </aside>

      {/* Chain */}
      <section className="flex min-w-0 flex-1 flex-col">
        <header className="mb-3 flex items-center gap-2">
          <h2 className="text-base font-semibold text-ink-100">
            {detail?.name ?? 'Workflow Templates'}
          </h2>
          {detail && !detail.is_default && (
            <button
              onClick={onSetDefault}
              className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] text-ink-400 hover:bg-ink-800 hover:text-ink-200"
            >
              <Star size={11} /> Set default
            </button>
          )}
          <div className="ml-auto flex items-center gap-2">
            {detail && (
              <button
                onClick={onDelete}
                className="rounded p-1.5 text-ink-500 hover:bg-rose-500/10 hover:text-rose-400"
                title="Delete template"
              >
                <Trash2 size={14} />
              </button>
            )}
            <button
              onClick={save}
              disabled={!dirty || saving}
              className="rounded-md border px-3 py-1.5 text-[13px] transition disabled:opacity-40"
              style={{
                background: 'var(--accent-soft)',
                color: 'var(--accent-text)',
                borderColor: 'var(--accent-border)',
              }}
              data-testid="workflow-save-template"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </header>

        <div className="flex min-h-0 flex-1 flex-wrap content-start items-start gap-2 overflow-y-auto">
          {runs.map((run, ri) => {
            const capsules = run.items.map((d) => (
              <NodeCapsule
                key={d._key}
                node={d}
                active={d._key === selectedNodeKey}
                onSelect={() => setSelectedNodeKey(d._key)}
                onDragStart={() => setDragKey(d._key)}
                onDrop={() => onDrop(d._key)}
              />
            ));
            if (run.group != null) {
              return (
                <div
                  key={`run${ri}`}
                  className="flex flex-col gap-1.5 rounded-lg border border-dashed border-line-strong p-1.5"
                  title={`Parallel group ${run.group}`}
                >
                  {capsules}
                </div>
              );
            }
            return <React.Fragment key={`run${ri}`}>{capsules}</React.Fragment>;
          })}

          <button
            onClick={() => setLibraryOpen(true)}
            disabled={!selectedId}
            className="inline-flex items-center gap-1 self-start rounded-full border border-dashed border-line-strong px-3 py-1.5 text-[13px] text-ink-400 transition hover:border-[var(--accent-border)] hover:text-ink-200 disabled:opacity-40"
            data-testid="workflow-add-from-library"
          >
            <Plus size={14} /> Add from library
          </button>
        </div>
      </section>

      {/* Inspector */}
      <aside className="flex w-80 shrink-0 flex-col border-l border-line pl-4">
        {selectedNode ? (
          <>
            <div className="mb-3 flex gap-1 border-b border-line">
              {(['info', 'flow', 'events'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setInspectorTab(tab)}
                  className={`-mb-px border-b-2 px-2.5 py-1.5 text-[12px] transition ${
                    inspectorTab === tab
                      ? 'border-[var(--accent-border)] text-ink-100'
                      : 'border-transparent text-ink-500 hover:text-ink-300'
                  }`}
                >
                  {tab === 'info' ? 'Node Info' : tab === 'flow' ? 'Flow Rules' : 'Events'}
                </button>
              ))}
            </div>
            {inspectorTab === 'info' && (
              <NodeInfoTab
                node={selectedNode}
                people={people}
                agents={agents}
                onPatch={(patch) => patchNode(selectedNode._key, patch)}
                onRemove={() => removeNode(selectedNode._key)}
              />
            )}
            {inspectorTab === 'flow' && <FlowRulesTab />}
            {inspectorTab === 'events' && <EventsTab />}
          </>
        ) : (
          <div className="flex h-full items-center justify-center text-center text-[13px] text-ink-600">
            {detail ? 'Select a node to edit' : 'Select or create a template'}
          </div>
        )}
      </aside>

      {libraryOpen && (
        <LibraryPickerModal
          items={library}
          onPick={addFromLibrary}
          onClose={() => setLibraryOpen(false)}
        />
      )}
    </div>
  );
};

// ── capsule ────────────────────────────────────────────────────────────────

const NodeCapsule: React.FC<{
  node: DraftNode;
  active: boolean;
  onSelect: () => void;
  onDragStart: () => void;
  onDrop: () => void;
}> = ({ node, active, onSelect, onDragStart, onDrop }) => (
  <button
    draggable
    onDragStart={onDragStart}
    onDragOver={(e) => e.preventDefault()}
    onDrop={onDrop}
    onClick={onSelect}
    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] transition ${
      active
        ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
        : 'border-line text-ink-300 hover:border-line-strong'
    } ${node.skip_default ? 'line-through opacity-50' : ''}`}
    data-testid="workflow-node-capsule"
  >
    {node.name}
    {node.review_required && <span className="text-[10px] text-purple-400">✓</span>}
  </button>
);

// ── inspector tabs ───────────────────────────────────────────────────────────

const Toggle: React.FC<{
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}> = ({ label, checked, onChange }) => (
  <label className="flex items-center justify-between py-1.5 text-[13px] text-ink-300">
    <span>{label}</span>
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 rounded-full transition ${
        checked ? 'bg-[var(--accent, #6366f1)]' : 'bg-ink-700'
      }`}
      role="switch"
      aria-checked={checked}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
          checked ? 'left-[1.125rem]' : 'left-0.5'
        }`}
      />
    </button>
  </label>
);

const NodeInfoTab: React.FC<{
  node: DraftNode;
  people: PersonOption[];
  agents: AgentOption[];
  onPatch: (patch: Partial<DraftNode>) => void;
  onRemove: () => void;
}> = ({ node, people, agents, onPatch, onRemove }) => (
  <div className="flex flex-col gap-3 overflow-y-auto text-[13px]">
    <div>
      <label className="mb-1 block text-[11px] uppercase tracking-wider text-ink-600">Name</label>
      <input
        value={node.name}
        onChange={(e) => onPatch({ name: e.target.value })}
        className="h-8 w-full rounded-md border border-line bg-transparent px-2 text-ink-100 focus:border-line-strong focus:outline-none"
      />
    </div>

    <div>
      <label className="mb-1 block text-[11px] uppercase tracking-wider text-ink-600">
        Default owner
      </label>
      <OwnerPicker
        mode="owner"
        people={people}
        agents={agents}
        ownerUserId={node.default_owner_user_id}
        ownerAgentId={node.default_owner_agent_id}
        onOwnerChange={(ref) =>
          onPatch({
            default_owner_user_id: ref?.user_id ?? null,
            default_owner_agent_id: ref?.agent_id ?? null,
          })
        }
      />
    </div>

    <div>
      <label className="mb-1 block text-[11px] uppercase tracking-wider text-ink-600">
        Default members
      </label>
      <OwnerPicker
        mode="members"
        people={people}
        agents={agents}
        members={node.members}
        onMembersChange={(members) => onPatch({ members })}
      />
    </div>

    <div>
      <label className="mb-1 block text-[11px] uppercase tracking-wider text-ink-600">
        Duration
      </label>
      <div className="flex h-8 items-center rounded-md border border-dashed border-line px-2 text-ink-600">
        Not set — fill in project
      </div>
    </div>

    <div>
      <label className="mb-1 block text-[11px] uppercase tracking-wider text-ink-600">
        Deliverable
      </label>
      <input
        value={node.deliverable_label ?? ''}
        onChange={(e) => onPatch({ deliverable_label: e.target.value || null })}
        placeholder="e.g. Final script"
        className="h-8 w-full rounded-md border border-line bg-transparent px-2 text-ink-100 placeholder-ink-600 focus:border-line-strong focus:outline-none"
      />
    </div>

    <div className="border-t border-line pt-1">
      <Toggle
        label="Skip by default"
        checked={node.skip_default}
        onChange={(v) => onPatch({ skip_default: v })}
      />
      <Toggle
        label="Review required"
        checked={node.review_required}
        onChange={(v) => onPatch({ review_required: v })}
      />
      <Toggle
        label="Deliverable required"
        checked={node.deliverable_required}
        onChange={(v) => onPatch({ deliverable_required: v })}
      />
    </div>

    <button
      onClick={onRemove}
      className="mt-2 inline-flex items-center gap-1.5 self-start rounded-md px-2 py-1 text-[12px] text-rose-400 hover:bg-rose-500/10"
    >
      <Trash2 size={13} /> Remove node
    </button>
  </div>
);

const FlowRulesTab: React.FC = () => (
  <div className="flex flex-col gap-3 overflow-y-auto text-[13px] text-ink-300">
    <p className="text-[12px] text-ink-500">
      Fixed conventions in M1 (configurable in a later milestone).
    </p>
    <RuleRow title="Completed by" body="The node owner themselves (a manager may override)." />
    <RuleRow
      title="Conditions"
      body="Owner assigned · owner review (when Review required) · deliverable filed (when Deliverable required, hard-checked)."
    />
  </div>
);

const EventsTab: React.FC = () => (
  <div className="flex flex-col gap-3 overflow-y-auto text-[13px] text-ink-300">
    <p className="text-[12px] text-ink-500">Built-in events in M1 (configurable later).</p>
    <RuleRow title="On arrival" body="Idempotently derive the mirror issue for this node." />
    <RuleRow title="On completion" body="Close the mirror issue. No agent will start automatically." />
  </div>
);

const RuleRow: React.FC<{ title: string; body: string }> = ({ title, body }) => (
  <div className="rounded-md border border-line px-2.5 py-2">
    <div className="mb-0.5 text-[12px] font-medium text-ink-200">{title}</div>
    <div className="text-[12px] leading-relaxed text-ink-500">{body}</div>
  </div>
);
