import type { NodeTypes } from '@xyflow/react';
import { ChapterNode } from './ChapterNode';
import { ChapterFlowNode } from './ChapterFlowNode';

export { ChapterNode } from './ChapterNode';
export { ChapterFlowNode } from './ChapterFlowNode';

export const scriptNodeTypes: NodeTypes = {
  chapterNode: ChapterNode,
  chapterFlowNode: ChapterFlowNode,
};
