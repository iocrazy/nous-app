// An inline ASSET chip inside a prompt body.
//
// Sibling of `PromptImageChipNode` and the same `atom: true` bargain: the chip
// is one indivisible thing, so the caret steps over it, backspace removes all
// of it, and the document can therefore double as the mention list
// (`collectAssetRefs`).
//
// What it shows is the asset's NAME. What it projects into the persisted text
// is the `@[asset:<id>]` token — see `mentionedAssets.ts` for why the two
// differ. The cover thumbnail goes through `mediaSrc()` for the #1898 reason:
// a bare relative `/api/v1/...` src resolves against the frontend origin,
// which is a different host from the API in production, and same-origin dev
// cannot see the breakage.

import React from 'react';
import { Node, mergeAttributes } from '@tiptap/core';
import { NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps } from '@tiptap/react';
import { X } from 'lucide-react';

import { ASSET_TYPE_ICON } from '../../../../components/resources/assets/assetTypeMeta';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import type { MentionedAsset } from '../mentionedAssets';
import { mediaSrc } from '../mediaUrl';
import { PROMPT_ASSET_REF } from './promptImageRefs';

function PromptAssetChipView({ node, deleteNode, editor }: NodeViewProps): React.ReactElement {
  const { asset_id, name, asset_type, cover_file_id } = node.attrs as MentionedAsset;
  // An asset with no cover is not an error — a prompt asset never has one.
  // Its type icon carries the same information the picture would.
  const Icon = ASSET_TYPE_ICON[asset_type] ?? ASSET_TYPE_ICON.prop;
  return (
    <NodeViewWrapper as="span" className="inline-block align-baseline">
      <span
        data-testid="prompt-asset-chip"
        data-asset-id={asset_id}
        className="mx-0.5 inline-flex items-center gap-1 rounded border border-canvas-line bg-canvas-raise px-1 py-0.5 align-baseline text-[12px] text-canvas-text select-none"
      >
        {cover_file_id ? (
          <img
            src={mediaSrc(getResourceCoverUrl(cover_file_id))}
            alt=""
            aria-hidden="true"
            className="h-4 w-4 shrink-0 rounded-full object-cover"
          />
        ) : (
          <Icon size={11} className="shrink-0 text-canvas-muted" />
        )}
        <span className="max-w-[12rem] truncate font-medium">{name}</span>
        {editor.isEditable && (
          <button
            type="button"
            onClick={deleteNode}
            aria-label={`Remove ${name}`}
            className="ml-0.5 opacity-50 transition-opacity hover:opacity-100"
          >
            <X size={10} />
          </button>
        )}
      </span>
    </NodeViewWrapper>
  );
}

export const PromptAssetChipNode = Node.create({
  name: PROMPT_ASSET_REF,
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: false,

  addAttributes: () => ({
    asset_id: { default: '' },
    name: { default: '' },
    asset_type: { default: 'prop' },
    cover_file_id: { default: null },
  }),

  parseHTML: () => [{ tag: 'span[data-prompt-asset-id]' }],

  renderHTML: ({ HTMLAttributes }) => [
    'span',
    mergeAttributes(HTMLAttributes, {
      'data-prompt-asset-id': HTMLAttributes.asset_id,
      class: 'prompt-asset-chip',
    }),
    HTMLAttributes.name ?? '',
  ],

  addNodeView: () => ReactNodeViewRenderer(PromptAssetChipView),
});
