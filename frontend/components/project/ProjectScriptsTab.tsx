import { FileText } from 'lucide-react';

interface Props {
  projectId: string;
}

export function ProjectScriptsTab({ projectId }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
      <FileText size={40} className="text-zinc-700" />
      <p className="text-sm text-zinc-500">Script Editor coming in P2</p>
      <p className="text-xs text-zinc-600">AI-powered story creation with chapter branching</p>
    </div>
  );
}
