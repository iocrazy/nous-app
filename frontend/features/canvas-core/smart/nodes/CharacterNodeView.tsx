/**
 * Character canvas bible-card node (character canvas epic PR-CC2).
 *
 * A SOURCE card: portrait thumb + name + role badge + description excerpt.
 * The preset agent workflow hangs its prompt branches off the right handle.
 * Name/description are editable inline (nodrag inputs) so an unbound card is
 * still useful; a bound card (character_id set) carries the library row's data.
 */

import { mediaSrc } from '../mediaUrl';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { UserRound } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { UnmigratedBadge } from './UnmigratedBadge';

import type { CharacterNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';

const ROLE_TONE: Record<string, string> = {
  lead: 'bg-indigo-500/15 text-indigo-500',
  support: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  antagonist: 'bg-rose-500/15 text-rose-500',
};

export function CharacterNodeView({ id, data, selected }: NodeProps) {
  const { name, role_tag, description, portrait_url, unmigrated } =
    data as unknown as CharacterNodeData;
  const { t } = useTranslation();
  const patch = useNodeDataPatch(id);
  // Both fields write the canvas document. `readOnly` (not `disabled`):
  // a viewer's whole purpose is reading, and a disabled field can't be
  // focused, selected or copied — see useCanvasReadOnly.
  const readOnly = useCanvasReadOnly();

  return (
    <div
      data-testid="smart-character-node"
      className={`mh-node border-canvas-line ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.character }}
    >
      <div className="mh-node-head">
        <div className="mh-node-title">Character</div>
        {unmigrated && <UnmigratedBadge t={t} />}
        {role_tag && (
          <span
            data-testid="character-role-badge"
            className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
              ROLE_TONE[role_tag] ?? 'bg-canvas-line/40 text-canvas-muted'
            }`}
          >
            {role_tag}
          </span>
        )}
      </div>
      <div className="flex gap-2.5 p-3">
        {/* 3:4 portrait thumb — placeholder silhouette when the character has
            no portrait yet (the portrait branch generates one). */}
        <div className="h-24 w-[72px] shrink-0 overflow-hidden rounded-lg border border-canvas-line bg-canvas-line/20">
          {portrait_url ? (
            <img
              data-testid="character-portrait"
              src={mediaSrc(portrait_url)}
              alt={name}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-canvas-muted">
              <UserRound size={24} />
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <input
            className="nodrag w-full bg-transparent text-[13px] font-semibold text-ink-100 outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 read-only:opacity-80 read-only:cursor-default"
            value={name}
            onChange={(e) => patch({ name: e.target.value })}
            placeholder="Character name"
            aria-label="Character name"
            readOnly={readOnly}
          />
          <textarea
            className="nodrag nowheel mt-1 h-16 w-full resize-none bg-transparent text-[11px] leading-snug text-canvas-text outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 read-only:opacity-80 read-only:cursor-default"
            value={description}
            onChange={(e) => patch({ description: e.target.value })}
            placeholder="Bio, look, temperament…"
            aria-label="Character description"
            readOnly={readOnly}
          />
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
