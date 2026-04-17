import type { NodeTypes } from '@xyflow/react';
import { ChapterNode } from './ChapterNode';
import { ChapterFlowNode } from './ChapterFlowNode';
import { BranchNode } from './BranchNode';
import { StoryRootNode } from './StoryRootNode';

export { ChapterNode } from './ChapterNode';
export { ChapterFlowNode } from './ChapterFlowNode';
export { BranchNode } from './BranchNode';
export { StoryRootNode } from './StoryRootNode';

export const scriptNodeTypes: NodeTypes = {
  chapterNode: ChapterNode,
  chapterFlowNode: ChapterFlowNode,
  branchNode: BranchNode,
  storyRootNode: StoryRootNode,
};
