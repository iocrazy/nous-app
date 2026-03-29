import { useParams } from 'react-router-dom';
import { ScriptEditorPage } from './ScriptEditorPage';

export function ScriptEditor() {
  const { scriptId } = useParams<{ scriptId: string }>();
  return <ScriptEditorPage />;
}
