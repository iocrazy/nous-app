/**
 * ClassicMode inline node-config editor (Phase 5a).
 *
 * Renders the editable `data` fields for a node type (see `nodeFieldSpec.ts`)
 * as `ink-*` inputs/textareas inside the node shell body. Each field writes
 * its value straight to `node.data[key]` via `useNodeDataPatch` on every
 * keystroke (onChange) so a configured node can ACTUALLY run with real params.
 *
 * The field `key`s are the EXACT keys the backend reads — so editing the
 * `prompt` of an image_gen node, the `workflow_slug` of a comfy node, etc.
 * directly feeds `_extract_*_params` / `resolve_provider_slug`.
 *
 * React Flow interaction guards:
 *   - every input carries `nodrag` (and textareas `nowheel`) so React Flow
 *     does not start a node drag / canvas zoom while editing, and
 *   - pointer/mouse-down events are stopped from bubbling so a click into a
 *     field doesn't select/drag the underlying node.
 *
 * Focus is preserved across keystrokes because the node view is a stable
 * module-scope component (no remount) and the input keeps its identity — the
 * same pattern SmartMode's PromptNodeView uses. The patch merges into
 * `node.data` (store `patchNode` does a shallow data merge), the new value
 * flows back in as the `data` prop, and React reuses the existing input.
 */

import type { NodeFieldSpec } from './nodeFieldSpec';
import { getNodeFieldSpec } from './nodeFieldSpec';
import { useNodeDataPatch } from '../../smart/nodes/useNodeDataPatch';

/** Stop a pointer/mouse interaction from reaching the React Flow node. */
function stopNodeInteraction(e: { stopPropagation: () => void }) {
  e.stopPropagation();
}

/** Coerce an opaque data value into a controlled-input string. */
function fieldValue(data: Record<string, unknown>, key: string): string {
  const raw = data[key];
  return raw == null ? '' : String(raw);
}

const FIELD_LABEL_CLASS = 'text-[10px] uppercase tracking-wide text-ink-400';
const INPUT_CLASS =
  'nodrag w-full rounded border border-ink-700 bg-ink-950 px-2 py-1 text-[11px] ' +
  'text-ink-100 outline-none placeholder:text-ink-500 focus:ring-1 focus:ring-indigo-400 ' +
  'disabled:opacity-60';
const TEXTAREA_CLASS =
  'nodrag nowheel min-h-[3rem] w-full resize-y rounded border border-ink-700 bg-ink-950 ' +
  'px-2 py-1 text-[11px] text-ink-100 outline-none placeholder:text-ink-500 ' +
  'focus:ring-1 focus:ring-indigo-400 disabled:opacity-60';

function NodeField({
  type,
  field,
  value,
  disabled,
  onPatch,
}: {
  type: string;
  field: NodeFieldSpec;
  value: string;
  disabled: boolean;
  onPatch: (next: string) => void;
}) {
  const testid = `classic-field-${type}-${field.key}`;
  const shared = {
    'data-testid': testid,
    'aria-label': field.label,
    value,
    placeholder: field.placeholder,
    disabled,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      onPatch(e.target.value),
    // Don't let a click/drag in the field move or select the node.
    onPointerDown: stopNodeInteraction,
    onMouseDown: stopNodeInteraction,
  };

  return (
    <label className="flex flex-col gap-0.5">
      <span className={FIELD_LABEL_CLASS}>{field.label}</span>
      {field.kind === 'textarea' ? (
        <textarea className={TEXTAREA_CLASS} rows={2} {...shared} />
      ) : (
        <input
          type={field.kind === 'number' ? 'number' : 'text'}
          className={INPUT_CLASS}
          {...shared}
        />
      )}
    </label>
  );
}

/**
 * Inline config editor for a classic node. Renders nothing for display-only
 * node types (no field spec). When `disabled` (e.g. the node is running) the
 * fields stay visible but read-only so editing can't race the in-flight run.
 */
export function NodeConfigFields({
  id,
  type,
  data,
  disabled = false,
}: {
  id: string;
  type: string;
  data: unknown;
  disabled?: boolean;
}) {
  const patch = useNodeDataPatch(id);
  const fields = getNodeFieldSpec(type);
  if (fields.length === 0) return null;

  const obj = (data ?? {}) as Record<string, unknown>;

  return (
    <div className="flex flex-col gap-2" data-testid={`classic-node-${type}-config`}>
      {fields.map((field) => (
        <NodeField
          key={field.key}
          type={type}
          field={field}
          value={fieldValue(obj, field.key)}
          disabled={disabled}
          onPatch={(next) => patch({ [field.key]: next })}
        />
      ))}
    </div>
  );
}
