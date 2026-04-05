import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { BookOpen } from 'lucide-react';
import type { Node } from '@xyflow/react';

interface StoryRootData {
  title: string;
  chapterCount: number;
  genre: string;
  [key: string]: unknown;
}

export type StoryRootNode = Node<StoryRootData>;

export const StoryRootNode = memo(({ data }: NodeProps<StoryRootNode>) => {
  return (
    <div className="w-[200px] rounded-xl border border-zinc-600 bg-zinc-800/80 shadow-lg">
      {/* Story Name */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-zinc-700">
        <BookOpen size={14} className="text-indigo-400 shrink-0" />
        <span className="text-sm font-semibold text-zinc-100 truncate">
          {data.title || 'Untitled Story'}
        </span>
      </div>

      {/* Meta Row */}
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="text-[11px] text-zinc-400">
          {data.chapterCount ?? 0} {data.chapterCount === 1 ? 'chapter' : 'chapters'}
        </span>
        {data.genre && (
          <span className="ml-auto text-[10px] px-2 py-0.5 rounded bg-indigo-600/20 text-indigo-400 font-medium truncate max-w-[80px]">
            {data.genre}
          </span>
        )}
      </div>

      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-3 !h-3 !bg-indigo-500"
      />
    </div>
  );
});

StoryRootNode.displayName = 'StoryRootNode';
