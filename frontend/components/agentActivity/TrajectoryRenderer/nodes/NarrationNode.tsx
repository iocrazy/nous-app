/**
 * 一段阶段性叙述（3c §4.1）。与最终回答共用 `MarkdownBody`——同一个说话人在同一个
 * 回合里说的话，用两套排版会读成两个不同的东西。
 */
import React from 'react';

import { MarkdownBody } from '../../../AILibrary/MarkdownBody';
import type { NarrationNode } from '../foldEvents';
import { registerTrajectoryNode, type NodeProps } from './registry';

export const NarrationNodeView: React.FC<NodeProps<NarrationNode>> = ({ node }) => (
  <div className="px-2.5 py-1" data-testid="traj-narration" data-step={node.step ?? undefined}>
    <MarkdownBody source={node.text} className="text-[13px]" />
  </div>
);

registerTrajectoryNode('narration', NarrationNodeView);

export default NarrationNodeView;
