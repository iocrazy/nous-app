// An inline image chip inside a prompt body (IC's `mention-image-token`).
//
// `atom: true` is the whole point: the chip is one indivisible thing. The
// caret steps over it, backspace deletes all of it, and it can never be split
// in half by typing — which is what lets the document double as the reference
// image list (promptImageRefs.ts).
//
// The thumbnail needs no auth (/generated-media/{id}/cover is public), but it
// DOES need `mediaSrc()`: a bare relative `/api/v1/...` src resolves against
// the frontend origin, which in production is a different host from the API —
// the #1898 broken-image class. Same-origin dev and e2e cannot see this.

import React from 'react';
import { Node, mergeAttributes } from '@tiptap/core';
import { NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps } from '@tiptap/react';
import { X } from 'lucide-react';

import { mediaSrc } from '../mediaUrl';
import { PROMPT_IMAGE_REF } from './promptImageRefs';

function PromptImageChipView({ node, deleteNode, editor }: NodeViewProps): React.ReactElement {
  const { url, alias, kind } = node.attrs as { url: string; alias: string; kind: string };
  return (
    <NodeViewWrapper as="span" className="inline-block align-baseline">
      <span
        data-testid="prompt-image-chip"
        // Semantic tokens, not raw hues — see the palette convention in
        // CLAUDE.md (the old indigo/amber names lost their meaning in K1).
        className="mx-0.5 inline-flex items-center gap-1 rounded border border-canvas-line bg-canvas-raise px-1 py-0.5 align-baseline text-[12px] text-canvas-text select-none"
      >
        <img
          src={mediaSrc(url)}
          alt=""
          aria-hidden="true"
          // Round, like IC's mention token.
          className="h-4 w-4 shrink-0 rounded-full object-cover"
        />
        <span className="max-w-[12rem] truncate font-medium">{alias}</span>
        {editor.isEditable && (
          <button
            type="button"
            onClick={deleteNode}
            aria-label={`Remove ${alias}`}
            className="ml-0.5 opacity-50 transition-opacity hover:opacity-100"
          >
            <X size={10} />
          </button>
        )}
      </span>
      {/* kind rides along for the runner; nothing renders it */}
      <span hidden data-kind={kind} />
    </NodeViewWrapper>
  );
}

export const PromptImageChipNode = Node.create({
  name: PROMPT_IMAGE_REF,
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: false,

  addAttributes: () => ({
    url: { default: '' },
    alias: { default: '' },
    kind: { default: 'image' },
  }),

  parseHTML: () => [{ tag: 'span[data-prompt-image-url]' }],

  renderHTML: ({ HTMLAttributes }) => [
    'span',
    mergeAttributes(HTMLAttributes, {
      'data-prompt-image-url': HTMLAttributes.url,
      class: 'prompt-image-chip',
    }),
    HTMLAttributes.alias ?? '',
  ],

  addNodeView: () => ReactNodeViewRenderer(PromptImageChipView),
});
