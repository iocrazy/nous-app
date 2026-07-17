import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

// The SCRIPT_V2 flag was retired (2026-07-07): the legacy editor is gone and the
// route now unconditionally mounts the v2 EditorShell. These tests pin that.
vi.mock('../components/EditorShell', () => ({
  EditorShell: ({ scriptId }: { scriptId: string }) => (
    <div data-testid="v2-shell">{scriptId}</div>
  ),
}));

let mockScriptId: string | undefined = '42';
vi.mock('react-router-dom', () => ({
  useParams: () => ({ scriptId: mockScriptId }),
}));
// Providers are irrelevant to the mount decision — make them pass-through.
vi.mock('../../components/Toast', () => ({
  ToastProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useToast: () => ({ addToast: () => {} }),
  useOptionalToast: () => null,
}));
vi.mock('../../contexts/TaskManagerContext', () => ({
  TaskManagerProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
// The route reads the current user (for collaboration presence); stub it so the
// mount assertion doesn't need a real AuthProvider.
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ currentUserId: null, userProfile: { name: 'Tester' } }),
}));

import { ScriptEditor } from '../../pages/ScriptEditor/index';

afterEach(() => {
  cleanup();
  mockScriptId = '42';
});

describe('ScriptEditor route', () => {
  it('mounts the v2 shell with the scriptId param', () => {
    render(<ScriptEditor />);
    const shell = screen.getByTestId('v2-shell');
    expect(shell).toBeInTheDocument();
    expect(shell).toHaveTextContent('42');
  });

  it('renders nothing when no scriptId is present', () => {
    mockScriptId = undefined;
    const { container } = render(<ScriptEditor />);
    expect(screen.queryByTestId('v2-shell')).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });
});
