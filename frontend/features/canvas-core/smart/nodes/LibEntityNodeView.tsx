/**
 * Location/prop library-card node (SP2) — the generalized sibling of
 * CharacterNodeView. One component, registered under both the `location`
 * and `prop` smart node types (the node's `type` picks the icon + title).
 * SOURCE card: cover thumb + name + badge + inline description edits;
 * the preset workflow hangs its prompt branches off the right handle.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { MapPin, Package } from 'lucide-react';

import type { LibEntityNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';

const META = {
  location: { title: 'Location', Icon: MapPin },
  prop: { title: 'Prop', Icon: Package },
} as const;

export function LibEntityNodeView({ id, type, data, selected }: NodeProps) {
  const { name, badge_tag, description, cover_url } =
    data as unknown as LibEntityNodeData;
  const patch = useNodeDataPatch(id);
  // Inline edits write the canvas document. Text fields take `readOnly`,
  // never `disabled` — a viewer must still be able to select and copy the
  // card's text. See useCanvasReadOnly.
  const readOnly = useCanvasReadOnly();
  const meta = META[(type as keyof typeof META) ?? 'location'] ?? META.location;
  const { Icon } = meta;

  return (
    <div
      data-testid={`smart-${type}-node`}
      className={`mh-node border-canvas-line ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.location }}
    >
      <div className="mh-node-head">
        <div className="mh-node-title">{meta.title}</div>
        {badge_tag && (
          <span
            data-testid="lib-entity-badge"
            className="rounded-full bg-canvas-line/40 px-2 py-0.5 text-[10px] font-medium text-canvas-muted"
          >
            {badge_tag}
          </span>
        )}
      </div>
      <div className="flex gap-2.5 p-3">
        {/* 16:9-ish cover thumb — placeholder icon until a branch renders one. */}
        <div className="h-16 w-24 shrink-0 overflow-hidden rounded-lg border border-canvas-line bg-canvas-line/20">
          {cover_url ? (
            <img
              data-testid="lib-entity-cover"
              src={cover_url}
              alt={name}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-canvas-muted">
              <Icon size={20} />
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <input
            className="nodrag w-full bg-transparent text-[13px] font-semibold text-ink-100 outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 read-only:opacity-80 read-only:cursor-default"
            value={name}
            onChange={(e) => patch({ name: e.target.value })}
            placeholder={`${meta.title} name`}
            aria-label={`${meta.title} name`}
            readOnly={readOnly}
          />
          <textarea
            className="nodrag nowheel mt-1 h-12 w-full resize-none bg-transparent text-[11px] leading-snug text-canvas-text outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 read-only:opacity-80 read-only:cursor-default"
            value={description}
            onChange={(e) => patch({ description: e.target.value })}
            placeholder="Look, mood, period, materials…"
            aria-label={`${meta.title} description`}
            readOnly={readOnly}
          />
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
