export const SCRIPT_NODE_TYPES = {
  chapter: 'chapterNode',
} as const;

export type ScriptNodeType = (typeof SCRIPT_NODE_TYPES)[keyof typeof SCRIPT_NODE_TYPES];
