/**
 * Generic ClassicMode node view (Phase 5a B4 + inline config).
 *
 * Used for the simple/passive MVP node types — `image`, `prompt`, `text`,
 * `llm`, `output`. They share one behaviour: render the halo + typed ports +
 * inline run_error via `ClassicNodeShell`. The EDITABLE types (`prompt` /
 * `text` / `llm`) additionally render `NodeConfigFields` so the user can
 * configure the node's run params (the editor writes to the exact `node.data`
 * keys the backend reads); display-only types (`image` / `output`) have no
 * field spec, so `NodeConfigFields` renders nothing for them.
 *
 * `comfy` is NOT built here — it has live timer + cancel UI of its own
 * (see RunnableOpNodeView).
 *
 * The `llm` type additionally renders its text RESULT inline once it has run:
 * the cascade patches the structured output onto `data.run_result`, and for an
 * llm node the generated text lives at `run_result.text` (mirrors the data-pipe
 * convention in dataPiping.ts). It renders below the config editor in a small
 * read-only scrollable frame — display only, NOT an editor.
 */

import type { NodeProps } from '@xyflow/react';

import type { ClassicNodeDefinition } from '../registry';
import { ClassicNodeShell } from './ClassicNodeShell';
import { NodeConfigFields } from './NodeConfigFields';
import { getNodeFieldSpec } from './nodeFieldSpec';
import { readRunData } from './classicNodeData';

/** Pull the inline text result off an llm node's structured run output, or
 *  null when absent / non-string / blank. */
function readResultText(data: unknown): string | null {
  const obj = (data ?? {}) as Record<string, unknown>;
  const result = obj.run_result;
  if (!result || typeof result !== 'object') return null;
  const text = (result as Record<string, unknown>).text;
  return typeof text === 'string' && text.trim() ? text.trim() : null;
}

export function makeClassicSimpleNodeView(def: ClassicNodeDefinition) {
  // Only the editable types (prompt/text/llm) carry a field spec; display-only
  // types (image/output) get nothing — so their shell body stays empty exactly
  // as before (no stray bordered strip).
  const hasFields = getNodeFieldSpec(def.type).length > 0;

  // Only the `llm` type surfaces an inline text result; the other simple types
  // emit no text run output (image/output are display-only; prompt/text are
  // passive sources whose value already lives in their own data).
  const showsTextResult = def.type === 'llm';

  function ClassicSimpleNodeView({ id, data, selected }: NodeProps) {
    const { run_status, run_error } = readRunData(data);
    const resultText =
      showsTextResult && run_status !== 'running' ? readResultText(data) : null;
    return (
      <ClassicNodeShell
        def={def}
        runStatus={run_status}
        runError={run_error}
        selected={Boolean(selected)}
      >
        {/* Editable types (prompt/text/llm) get inline config fields; disabled
            mid-run so edits can't race an in-flight run. Display-only types
            pass null so the shell renders no body. */}
        {hasFields ? (
          <NodeConfigFields
            id={id}
            type={def.type}
            data={data}
            disabled={run_status === 'running'}
          />
        ) : null}
        {/* llm text result — read-only inline display below the editor (never
            collides with the config fields). MVP: scrollable, height-capped. */}
        {resultText ? (
          <div
            className="mt-2 max-h-24 overflow-y-auto whitespace-pre-wrap break-words rounded border border-ink-700 bg-ink-950 p-2 text-[11px] leading-snug text-ink-100"
            data-testid={`classic-node-${def.type}-result`}
          >
            {resultText}
          </div>
        ) : null}
      </ClassicNodeShell>
    );
  }
  ClassicSimpleNodeView.displayName = `ClassicSimpleNodeView(${def.type})`;
  return ClassicSimpleNodeView;
}
