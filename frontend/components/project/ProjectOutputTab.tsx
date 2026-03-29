import { Download } from 'lucide-react';

interface Props {
  projectId: string;
}

export function ProjectOutputTab({ projectId }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
      <Download size={40} className="text-zinc-700" />
      <p className="text-sm text-zinc-500">Output & Export coming in P5</p>
      <p className="text-xs text-zinc-600">Export storyboards and scripts as PDF, ZIP, or video</p>
    </div>
  );
}
