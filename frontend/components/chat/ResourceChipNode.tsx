import React from 'react';
import { NodeViewWrapper, NodeViewProps, ReactNodeViewRenderer } from '@tiptap/react';
import { Node, mergeAttributes } from '@tiptap/core';
import { ResourceChipBody } from './ResourceChipBody';

/**
 * Inline resource chip.
 *
 * Newly picked assets no longer land here — they stage in the attachment row
 * above the composer. This node stays because content that already contains
 * chips (saved drafts, restored composer state) must keep rendering, and
 * `handleSend` still collects whatever chips it finds so those assets are
 * sent just as they always were.
 */
export function ResourceChipView({ node, deleteNode }: NodeViewProps): React.ReactElement {
  const { resourceId, name, kind, mime, thumbnailUrl, transcriptStatus, summaryStatus } = node.attrs;

  return (
    <NodeViewWrapper as="span" className="inline-block align-baseline">
      <ResourceChipBody
        variant="inline"
        resourceId={String(resourceId ?? '')}
        name={name}
        kind={kind}
        mime={mime}
        thumbnailUrl={thumbnailUrl}
        transcriptStatus={transcriptStatus}
        summaryStatus={summaryStatus}
        onRemove={deleteNode}
      />
    </NodeViewWrapper>
  );
}

export const ResourceRefNode = Node.create({
  name: 'resourceRef',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: true,
  // The three trailing attrs are additive: chips already sitting in saved
  // composer content carry only the first five, so every new one defaults to
  // an empty string and the view treats that as "nothing to show".
  addAttributes: () => ({
    resourceId: { default: '' },
    name: { default: '' },
    kind: { default: 'doc' },
    mime: { default: '' },
    scope: { default: { type: 'personal', id: '' } },
    /** Relative cover path from `/resources/search`; resolved at render. */
    thumbnailUrl: { default: '' },
    /** Status snapshot taken at insert time — the live Task Center reading
     *  refines it, but a chip must still say something without a provider. */
    transcriptStatus: { default: '' },
    summaryStatus: { default: '' },
  }),
  parseHTML: () => [{ tag: 'span[data-resource-id]' }],
  renderHTML: ({ HTMLAttributes }) =>
    ['span', mergeAttributes(HTMLAttributes, { 'data-resource-id': HTMLAttributes.resourceId,
                                                 class: 'resource-chip' }),
     `@${HTMLAttributes.name}`],
  addNodeView: () => ReactNodeViewRenderer(ResourceChipView),
  renderText: ({ node }) => `@${node.attrs.name}`,
});
