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
 */

import type { NodeProps } from '@xyflow/react';

import type { ClassicNodeDefinition } from '../registry';
import { ClassicNodeShell } from './ClassicNodeShell';
import { NodeConfigFields } from './NodeConfigFields';
import { getNodeFieldSpec } from './nodeFieldSpec';
import { readRunData } from './classicNodeData';

export function makeClassicSimpleNodeView(def: ClassicNodeDefinition) {
  // Only the editable types (prompt/text/llm) carry a field spec; display-only
  // types (image/output) get nothing — so their shell body stays empty exactly
  // as before (no stray bordered strip).
  const hasFields = getNodeFieldSpec(def.type).length > 0;

  function ClassicSimpleNodeView({ id, data, selected }: NodeProps) {
    const { run_status, run_error } = readRunData(data);
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
      </ClassicNodeShell>
    );
  }
  ClassicSimpleNodeView.displayName = `ClassicSimpleNodeView(${def.type})`;
  return ClassicSimpleNodeView;
}
