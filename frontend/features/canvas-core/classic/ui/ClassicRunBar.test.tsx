import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type {
  CascadeHandlers,
  CascadeReport,
  runClassicCascade,
} from '../cascade';
import { ClassicRunBar } from './ClassicRunBar';

type CascadeFn = typeof runClassicCascade;

// canvas-core has no toast of its own — the surface wires onToast to the app
// useToast. Stub it so the bar renders without a ToastProvider.
const addToast = vi.fn();
vi.mock('../../../../components/Toast', () => ({
  useToast: () => ({ addToast }),
}));

const EMPTY_REPORT: CascadeReport = {
  succeeded: [],
  failed: [],
  blocked: [],
  skipped: [],
  toasts: [],
};

beforeEach(() => {
  addToast.mockClear();
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'classic',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
    nodes: [
      { id: 'a', type: 'llm', data: { body: 'hi', run_status: 'idle' } },
      { id: 'b', type: 'llm', data: { body: 'yo', run_status: 'idle' } },
    ],
    connections: [{ id: 'e', source: 'a', target: 'b' }],
  });
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('ClassicRunBar — surface', () => {
  it('renders a Run button and the live node count', () => {
    render(<ClassicRunBar cascade={vi.fn()} />);
    expect(screen.getByRole('button', { name: /^run$/i })).toBeInTheDocument();
    expect(screen.getByText(/2 nodes/i)).toBeInTheDocument();
  });

  it('exposes an accessible toolbar name', () => {
    render(<ClassicRunBar cascade={vi.fn()} />);
    expect(
      screen.getByRole('toolbar', { name: /classic canvas run bar/i }),
    ).toBeInTheDocument();
  });
});

describe('ClassicRunBar — Run trigger', () => {
  it('invokes runClassicCascade with the live store nodes/connections + a runner + handlers', async () => {
    const cascade = vi.fn<CascadeFn>(async () => EMPTY_REPORT);
    render(<ClassicRunBar cascade={cascade} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    });

    await waitFor(() => expect(cascade).toHaveBeenCalledTimes(1));
    const [nodesArg, connsArg, runnerArg, handlers] = cascade.mock.calls[0];
    expect(nodesArg).toHaveLength(2);
    expect((nodesArg[0] as Record<string, unknown>).id).toBe('a');
    expect(connsArg).toHaveLength(1);
    expect(typeof runnerArg).toBe('function'); // createClassicBackendRunner output
    expect(typeof handlers.onNodePatch).toBe('function');
    expect(typeof handlers.onToast).toBe('function');
  });

  it('onNodePatch maps to store.patchNode(id, { data: patch })', async () => {
    const cascade = vi.fn<CascadeFn>(async () => EMPTY_REPORT);
    render(<ClassicRunBar cascade={cascade} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    });
    await waitFor(() => expect(cascade).toHaveBeenCalledTimes(1));
    const handlers = cascade.mock.calls[0][3] as CascadeHandlers;

    act(() => {
      handlers.onNodePatch('a', { run_status: 'succeeded', run_error: null });
    });

    const nodeA = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as Record<string, unknown>).id === 'a') as Record<
      string,
      Record<string, unknown>
    >;
    // patch merged into node.data (not replacing the existing body)
    expect(nodeA.data.run_status).toBe('succeeded');
    expect(nodeA.data.body).toBe('hi');
  });

  it('onToast maps to addToast(message, "error")', async () => {
    const cascade = vi.fn<CascadeFn>(async () => EMPTY_REPORT);
    render(<ClassicRunBar cascade={cascade} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    });
    await waitFor(() => expect(cascade).toHaveBeenCalledTimes(1));
    const handlers = cascade.mock.calls[0][3] as CascadeHandlers;

    act(() => handlers.onToast?.('Cascade stopped at Node A'));

    expect(addToast).toHaveBeenCalledWith('Cascade stopped at Node A', 'error');
  });

  it('disables the button while running and re-enables it when the cascade settles', async () => {
    let resolveCascade: (report: CascadeReport) => void = () => {};
    const cascade = vi.fn<CascadeFn>(
      () =>
        new Promise<CascadeReport>((resolve) => {
          resolveCascade = resolve;
        }),
    );
    render(<ClassicRunBar cascade={cascade} />);

    const idleBtn = screen.getByRole('button', { name: /^run$/i });
    expect(idleBtn).not.toBeDisabled();

    await act(async () => {
      fireEvent.click(idleBtn);
    });

    const runningBtn = screen.getByRole('button', { name: /running/i });
    expect(runningBtn).toBeDisabled();

    await act(async () => {
      resolveCascade(EMPTY_REPORT);
    });

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /^run$/i }),
      ).not.toBeDisabled(),
    );
    expect(cascade).toHaveBeenCalledTimes(1);
  });

  it('does not double-invoke the cascade on a second click while running', async () => {
    const cascade = vi.fn<CascadeFn>(() => new Promise<CascadeReport>(() => {}));
    render(<ClassicRunBar cascade={cascade} />);
    const btn = screen.getByRole('button', { name: /^run$/i });
    await act(async () => {
      fireEvent.click(btn);
    });
    // button is now disabled, but force a second invocation to prove the guard
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /running/i }));
    });
    expect(cascade).toHaveBeenCalledTimes(1);
  });

  it('catches a thrown cascade error and surfaces an error toast', async () => {
    const cascade = vi.fn<CascadeFn>(async () => {
      throw new Error('boom');
    });
    render(<ClassicRunBar cascade={cascade} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    });
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        expect.stringContaining('boom'),
        'error',
      ),
    );
    // button recovers to idle after the throw
    expect(screen.getByRole('button', { name: /^run$/i })).not.toBeDisabled();
  });
});
