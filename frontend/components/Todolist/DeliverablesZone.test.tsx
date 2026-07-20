import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DeliverablesZone } from './DeliverablesZone';

// i18n: echo interpolation so the dropzone hint's target is assertable.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (opts && 'target' in opts) return `Drop files — ${opts.target}`;
      if (opts && 'count' in opts) return `${opts.count} filed`;
      return key;
    },
  }),
}));

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

const fetchProjectFiles = vi.fn();
const uploadFile = vi.fn();
vi.mock('../../services/projectsService', () => ({
  fetchProjectFiles: (...a: unknown[]) => fetchProjectFiles(...a),
  uploadFile: (...a: unknown[]) => uploadFile(...a),
}));

function setup(over: Partial<React.ComponentProps<typeof DeliverablesZone>> = {}) {
  return render(
    <DeliverablesZone
      projectId="500"
      issueId={42}
      isStageMirror
      projectName="Launch Film"
      stageName="Script"
      {...over}
    />,
  );
}

describe('DeliverablesZone', () => {
  beforeEach(() => {
    fetchProjectFiles.mockReset().mockResolvedValue([]);
    uploadFile.mockReset().mockResolvedValue({ id: 'f1' });
  });

  it('fetches files scoped to the source issue on mount', async () => {
    setup();
    await waitFor(() =>
      expect(fetchProjectFiles).toHaveBeenCalledWith('500', false, null, '42'),
    );
  });

  it('names the project AND stage in the drop hint for a mirror issue', async () => {
    setup();
    await waitFor(() =>
      expect(screen.getByTestId('deliverables-dropzone')).toHaveTextContent(
        'Launch Film / Script',
      ),
    );
  });

  it('drops the stage from the hint for a non-mirror project issue', async () => {
    setup({ isStageMirror: false, stageName: undefined });
    await waitFor(() =>
      expect(screen.getByTestId('deliverables-dropzone')).toHaveTextContent(
        'Drop files — Launch Film',
      ),
    );
    expect(screen.getByTestId('deliverables-dropzone')).not.toHaveTextContent('/');
  });

  it('renders each filed file with a Filed chip', async () => {
    fetchProjectFiles.mockResolvedValue([
      { id: 'a', filename: 'cut-v1.mp4' },
      { id: 'b', filename: 'boards.pdf' },
    ]);
    setup();
    await waitFor(() =>
      expect(screen.getAllByTestId('deliverables-file-row')).toHaveLength(2),
    );
    expect(screen.getByText('cut-v1.mp4')).toBeInTheDocument();
  });

  it('uploads a browsed file with the source issue id', async () => {
    const { container } = setup();
    await waitFor(() => expect(fetchProjectFiles).toHaveBeenCalled());
    const input = container.querySelector('[data-testid="deliverables-input"]') as HTMLInputElement;
    const file = new File(['x'], 'take.mp4', { type: 'video/mp4' });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() =>
      expect(uploadFile).toHaveBeenCalledWith('500', file, undefined, '42'),
    );
  });
});
