import { useScriptCanvasStore } from '../../../stores/scriptCanvasStore';

interface ListViewProps {
  onNavigateToChapter: (nodeId: string) => void;
}

function estimateWordCount(text: string): number {
  if (!text) return 0;
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export function ListView({ onNavigateToChapter }: ListViewProps) {
  const { nodes, setViewMode } = useScriptCanvasStore();

  const chapterNodes = nodes.filter((n) => n.type === 'chapterNode');

  const handleRowClick = (nodeId: string) => {
    setViewMode('canvas');
    onNavigateToChapter(nodeId);
  };

  if (chapterNodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-zinc-500 gap-2">
        <p className="text-sm">No chapters yet</p>
        <p className="text-xs">Switch to Canvas view to add chapters</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col divide-y divide-zinc-800 overflow-y-auto h-full">
      {chapterNodes.map((node) => {
        const { title, summary, content, chapterNumber, branchLabel } = node.data;
        const wordCount = estimateWordCount(content);

        return (
          <button
            key={node.id}
            onClick={() => handleRowClick(node.id)}
            className="w-full text-left flex items-center gap-3 px-5 py-3 hover:bg-zinc-800/50 transition-colors group"
          >
            <span className="inline-flex items-center justify-center min-w-[22px] h-[22px] rounded-full bg-purple-600/20 text-purple-400 text-[11px] font-semibold border border-purple-500/30 px-1.5 shrink-0">
              {chapterNumber}
            </span>

            <span className="text-sm font-medium text-zinc-300 group-hover:text-white transition-colors shrink-0 w-36 truncate">
              {title || `Chapter ${chapterNumber}`}
            </span>

            {branchLabel && (
              <span className="text-[10px] text-amber-400 bg-amber-400/10 border border-amber-400/20 rounded px-1.5 py-0.5 leading-none shrink-0">
                {branchLabel}
              </span>
            )}

            <span className="text-[11px] text-zinc-500 flex-1 truncate">
              {summary || <span className="italic text-zinc-600">No summary</span>}
            </span>

            {wordCount > 0 && (
              <span className="text-[10px] text-zinc-600 shrink-0 tabular-nums">
                {wordCount.toLocaleString()} words
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
