import React from 'react';
import { useTranslation } from 'react-i18next';
import { NodeViewWrapper, NodeViewProps, ReactNodeViewRenderer } from '@tiptap/react';
import { Node, mergeAttributes } from '@tiptap/core';
import { FileText, Image, Video, Music, FileType2, X } from 'lucide-react';
import { useOptionalTaskManager } from '../../hooks/useOptionalTaskManager';
import {
  resolveChipProcessingState,
  resolveResourceThumbnailSrc,
  resourceProcessingState,
} from './resourceStatus';

const ICON_BY_KIND: Record<string, React.ComponentType<{ size?: number }>> = {
  video: Video, image: Image, audio: Music, doc: FileText, pdf: FileType2,
};

export function ResourceChipView({ node, deleteNode }: NodeViewProps): React.ReactElement {
  const { t } = useTranslation();
  const { resourceId, name, kind, mime, thumbnailUrl, transcriptStatus, summaryStatus } = node.attrs;
  const Icon = ICON_BY_KIND[kind] ?? FileText;
  const thumb = resolveResourceThumbnailSrc(thumbnailUrl);

  // Optional on purpose: the floating chat also mounts on the fullscreen
  // Script / Storyboard routes, which have no TaskManagerProvider. There the
  // chip falls back to the snapshot taken when it was inserted instead of
  // crashing the editor (RECON#18).
  const taskManager = useOptionalTaskManager();
  const snapshot = resourceProcessingState({ kind, mime, transcriptStatus, summaryStatus });
  const status = resolveChipProcessingState(snapshot, taskManager?.tasks, String(resourceId ?? ''));

  return (
    <NodeViewWrapper as="span" className="inline-block align-baseline">
      <span data-testid="resource-chip"
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border
                       border-agent-line bg-agent-soft text-agent text-[12px] mx-0.5 select-none">
        {thumb ? (
          <img data-testid="resource-chip-thumb" src={thumb} alt=""
               className="w-4 h-4 rounded object-cover" />
        ) : (
          <span data-testid="resource-chip-icon" className="inline-flex">
            <Icon size={11} />
          </span>
        )}
        <span className="font-medium">{name}</span>
        {status && (
          <span
            data-testid="resource-chip-status"
            data-status={status}
            title={t(
              status === 'processing'
                ? 'chat.mentionPicker.statusProcessing'
                : 'chat.mentionPicker.statusUnprocessed',
            )}
            className={`w-1.5 h-1.5 rounded-full bg-warn ${
              status === 'processing' ? 'animate-pulse' : ''
            }`}
          />
        )}
        <button onClick={deleteNode}
                className="opacity-50 hover:opacity-100 ml-0.5 inline-flex"
                aria-label="remove"><X size={10} /></button>
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
