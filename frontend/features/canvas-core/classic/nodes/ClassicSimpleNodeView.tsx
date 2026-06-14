/**
 * Generic ClassicMode node view (Phase 5a B4).
 *
 * Used for the non-interactive MVP node types — `image`, `prompt`, `llm`,
 * `output`. They share one behaviour: render the halo + typed ports +
 * inline run_error via `ClassicNodeShell`. Node-type-specific editing
 * (image picker, prompt textarea, etc.) lands in later slices; the MVP
 * just makes the typed graph wire-able and run-aware.
 *
 * `comfy` is NOT built here — it has live timer + cancel UI of its own
 * (see ComfyNodeView).
 */

import type { NodeProps } from '@xyflow/react';

import type { ClassicNodeDefinition } from '../registry';
import { ClassicNodeShell } from './ClassicNodeShell';
import { readRunData } from './classicNodeData';

export function makeClassicSimpleNodeView(def: ClassicNodeDefinition) {
  function ClassicSimpleNodeView({ data, selected }: NodeProps) {
    const { run_status, run_error } = readRunData(data);
    return (
      <ClassicNodeShell
        def={def}
        runStatus={run_status}
        runError={run_error}
        selected={Boolean(selected)}
      />
    );
  }
  ClassicSimpleNodeView.displayName = `ClassicSimpleNodeView(${def.type})`;
  return ClassicSimpleNodeView;
}
