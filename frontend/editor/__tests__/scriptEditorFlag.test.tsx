import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

// The flag router should mount exactly one of these; we stub both so the test
// asserts routing, not their internals.
vi.mock('../../pages/ScriptEditor/ScriptEditorPage', () => ({
  ScriptEditorPage: () => <div data-testid="legacy-editor" />,
}));
vi.mock('../components/EditorShell', () => ({
  EditorShell: ({ scriptId }: { scriptId: string }) => (
    <div data-testid="v2-shell">{scriptId}</div>
  ),
}));
vi.mock('react-router-dom', () => ({
  useParams: () => ({ scriptId: '42' }),
}));
// Providers are irrelevant to the routing decision — make them pass-through.
vi.mock('../../components/Toast', () => ({
  ToastProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('../../contexts/TaskManagerContext', () => ({
  TaskManagerProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
// The route reads the current user (for collaboration presence); stub it so the
// routing assertion doesn't need a real AuthProvider.
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ currentUserId: null, userProfile: { name: 'Tester' } }),
}));

import { ScriptEditor } from '../../pages/ScriptEditor/index';

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('ScriptEditor flag routing', () => {
  it('renders the legacy editor when the flag is off', () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_V2', 'false');
    render(<ScriptEditor />);
    expect(screen.getByTestId('legacy-editor')).toBeInTheDocument();
    expect(screen.queryByTestId('v2-shell')).not.toBeInTheDocument();
  });

  it('renders the v2 shell (with the scriptId param) when the flag is on', () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_V2', 'true');
    render(<ScriptEditor />);
    const shell = screen.getByTestId('v2-shell');
    expect(shell).toBeInTheDocument();
    expect(shell).toHaveTextContent('42');
    expect(screen.queryByTestId('legacy-editor')).not.toBeInTheDocument();
  });
});
