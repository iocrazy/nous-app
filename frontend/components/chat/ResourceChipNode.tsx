import React from 'react';
import { NodeViewWrapper, NodeViewProps, ReactNodeViewRenderer } from '@tiptap/react';
import { Node, mergeAttributes } from '@tiptap/core';
import { FileText, Image, Video, Music, FileType2 } from 'lucide-react';

const ICON_BY_KIND: Record<string, React.ComponentType<{ size?: number }>> = {
  video: Video, image: Image, audio: Music, doc: FileText, pdf: FileType2,
};

function ResourceChipView({ node, deleteNode }: NodeViewProps): React.ReactElement {
  const { name, kind } = node.attrs;
  const Icon = ICON_BY_KIND[kind] ?? FileText;
  return (
    <NodeViewWrapper as="span" className="inline-block align-baseline">
      <span data-testid="resource-chip"
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded
                       bg-indigo-700/60 text-indigo-100 text-[12px] mx-0.5 select-none">
        <Icon size={11} />
        <span className="font-medium">{name}</span>
        <button onClick={deleteNode}
                className="opacity-50 hover:opacity-100 ml-0.5"
                aria-label="remove">×</button>
      </span>
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
  addAttributes: () => ({
    resourceId: { default: '' },
    name: { default: '' },
    kind: { default: 'doc' },
    mime: { default: '' },
    scope: { default: { type: 'personal', id: '' } },
  }),
  parseHTML: () => [{ tag: 'span[data-resource-id]' }],
  renderHTML: ({ HTMLAttributes }) =>
    ['span', mergeAttributes(HTMLAttributes, { 'data-resource-id': HTMLAttributes.resourceId,
                                                 class: 'resource-chip' }),
     `📎 ${HTMLAttributes.name}`],
  addNodeView: () => ReactNodeViewRenderer(ResourceChipView),
  renderText: ({ node }) => `@${node.attrs.name}`,
});
