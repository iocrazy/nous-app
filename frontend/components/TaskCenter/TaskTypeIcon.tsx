import React from 'react';
import {
  Bot,
  Circle,
  Download,
  Eye,
  FileText,
  Image as ImageIcon,
  Music,
  RefreshCw,
  Search,
  Sparkles,
  Upload,
} from 'lucide-react';
import type { TaskType } from '../../contexts/TaskManagerContext';

/**
 * Lucide icon for a task type — replaces the legacy emoji-returning
 * `taskTypeIcon()` string helper so the Task Center renders consistent
 * vector icons instead of platform-dependent emoji glyphs.
 */
export const TaskTypeIcon: React.FC<{ type: TaskType; size?: number }> = ({
  type,
  size = 14,
}) => {
  switch (type) {
    case 'parse':
      return <Search size={size} />;
    case 'download':
      return <Download size={size} />;
    case 'upload':
      return <Upload size={size} />;
    case 'transcode':
      return <RefreshCw size={size} />;
    case 'thumbnail':
      return <ImageIcon size={size} />;
    case 'extract_audio':
      return <Music size={size} />;
    case 'ai_pipeline':
    case 'ai_summary':
      return <Sparkles size={size} />;
    case 'ai_extract':
      return <Eye size={size} />;
    case 'ai_transcription':
      return <FileText size={size} />;
    case 'agent':
    case 'agent_routine':
      return <Bot size={size} />;
    default:
      return <Circle size={size} />;
  }
};
