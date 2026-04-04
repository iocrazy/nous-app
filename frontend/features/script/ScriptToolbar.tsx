import { Plus, Undo2, Redo2 } from 'lucide-react';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';

interface Props {
  onCreateStory: () => void;
}

export function ScriptToolbar({ onCreateStory }: Props) {
  const addChapterNode = useScriptCanvasStore((s) => s.addChapterNode);
  const undo = useScriptCanvasStore((s) => s.undo);
  const redo = useScriptCanvasStore((s) => s.redo);

  const handleAddChapter = () => {
    const offsetX = 100 + Math.random() * 200;
    const offsetY = 100 + Math.random() * 200;
    addChapterNode({ x: offsetX, y: offsetY });
  };

  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-zinc-900 border-b border-zinc-800">
      <button
        onClick={handleAddChapter}
        className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
      >
        <Plus size={14} />
        Add Chapter
      </button>

      <button
        onClick={onCreateStory}
        className="flex items-center gap-1.5 bg-violet-600 hover:bg-violet-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
      >
        Generate Outline
      </button>

      <div className="flex-1" />

      <button onClick={() => undo()} className="p-1.5 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800" title="Undo">
        <Undo2 size={16} />
      </button>
      <button onClick={() => redo()} className="p-1.5 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800" title="Redo">
        <Redo2 size={16} />
      </button>
    </div>
  );
}
