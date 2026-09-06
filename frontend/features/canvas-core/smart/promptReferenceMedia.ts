/**
 * Pure builder for the reference-media half of a Send-to-Canvas insert:
 * given a prompt node that has just been created, produce the media node
 * carrying the sender's picture and the connection wiring it into the
 * prompt.
 *
 * `mediaUrl` is caller-supplied rather than derived here: when the caller
 * has minted a durable /api/v1/generated-media/ URL (via
 * importResourceAsCanvasMedia) that URL makes the node a real i2i/i2v
 * source (promptInputs.ts's DURABLE_PREFIXES accepts it). When minting
 * fails, the caller falls back to the resource cover URL instead — that
 * fallback is visual-only (not a matching prefix, so it's inert as a run
 * input), but keeps the picked asset visible on the canvas.
 *
 * No store access here — the caller owns committing the result
 * (setNodes/setConnections), which keeps this function trivial to unit
 * test.
 */

import { createMediaNode } from './factories';
import type { MediaNode } from './types';

export function buildPromptReferenceMedia(args: {
  promptNodeId: string;
  promptNodePosition: { x: number; y: number };
  mediaUrl: string;
  /** Kind of the media the caller resolved (importResourceAsCanvasMedia's
   *  result, or 'image' for the cover-fallback path — the cover endpoint
   *  only ever serves images). Drives the new media node's item kind so
   *  video assets don't render as a broken <img> and don't get treated as
   *  an image i2i source. */
  mediaKind: 'image' | 'video';
  /** Display name for the media item (the sender's filename). */
  name: string;
}): {
  mediaNode: MediaNode;
  connection: { id: string; source: string; target: string };
} {
  const { promptNodeId, promptNodePosition, mediaUrl, mediaKind, name } = args;

  const mediaNode = createMediaNode(
    {
      items: [{ url: mediaUrl, kind: mediaKind, name }],
    },
    {
      // Sits to the left of (and slightly above) the prompt node so the
      // auto-wired connection reads left-to-right without overlapping it.
      position: { x: promptNodePosition.x - 320, y: promptNodePosition.y - 40 },
    },
  );

  const connection = {
    id: `conn-${mediaNode.id}-${promptNodeId}`,
    source: mediaNode.id,
    target: promptNodeId,
  };

  return { mediaNode, connection };
}
