import { useScriptCanvasStore } from '../../../stores/scriptCanvasStore';

interface GridViewProps {
  onNavigateToChapter: (nodeId: string) => void;
}

export function GridView({ onNavigateToChapter }: GridViewProps) {
  const { nodes, setViewMode } = useScriptCanvasStore();

  const chapterNodes = nodes.filter((n) => n.type === 'chapterNode');

  const handleCardClick = (nodeId: string) => {
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
    <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 p-6 overflow-y-auto h-full">
      {chapterNodes.map((node) => {
        const { title, summary, chapterNumber, branchLabel } = node.data;

        return (
          <button
            key={node.id}
            onClick={() => handleCardClick(node.id)}
            className="text-left bg-zinc-900 border border-zinc-800 rounded-lg p-4 hover:border-zinc-600 hover:bg-zinc-800/60 transition-all duration-150 group"
          >
            <div className="flex items-start gap-2 mb-2">
              <span className="inline-flex items-center justify-center min-w-[22px] h-[22px] rounded-full bg-purple-600/20 text-purple-400 text-[11px] font-semibold border border-purple-500/30 px-1.5 shrink-0">
                {chapterNumber}
              </span>
              {branchLabel && (
                <span className="text-[10px] text-amber-400 bg-amber-400/10 border border-amber-400/20 rounded px-1.5 py-0.5 leading-none mt-0.5 shrink-0">
                  {branchLabel}
                </span>
              )}
            </div>

            <h3 className="text-sm font-medium text-zinc-200 mb-1.5 group-hover:text-white transition-colors line-clamp-2">
              {title || `Chapter ${chapterNumber}`}
            </h3>

            {summary ? (
              <p className="text-[11px] text-zinc-500 line-clamp-3 leading-relaxed">
                {summary}
              </p>
            ) : (
              <p className="text-[11px] text-zinc-600 italic">No summary</p>
            )}
          </button>
        );
      })}
    </div>
  );
}
