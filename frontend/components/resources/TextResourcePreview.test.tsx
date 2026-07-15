// frontend/components/resources/TextResourcePreview.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('./MarkdownResourceEditor', () => ({
  MarkdownResourceEditor: ({ value, readOnly }: { value: string; readOnly?: boolean }) => (
    <div data-testid="md-editor" data-readonly={String(!!readOnly)}>{value}</div>
  ),
}));
vi.mock('./PlainTextResourceEditor', () => ({
  PlainTextResourceEditor: ({ value, readOnly }: { value: string; readOnly?: boolean }) => (
    <div data-testid="code-editor" data-readonly={String(!!readOnly)}>{value}</div>
  ),
}));
const saveNew = vi.fn(async () => ({ id: 'v2' }));
const overwrite = vi.fn(async () => ({ id: 'v1' }));
vi.mock('../../services/resourceService', () => ({
  saveTextAsNewVersion: (...a: unknown[]) => saveNew(...a),
  overwriteVersionContent: (...a: unknown[]) => overwrite(...a),
  fetchResourceVersions: async () => [{ id: 'v1', version_number: 1 }],
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }) }));

import { TextResourcePreview } from './TextResourcePreview';

const res = (over: Record<string, unknown> = {}) => ({
  id: '10', filename: 'notes.md', mime_type: 'text/markdown',
  file_size_bytes: 20, current_version: 1, ...over,
}) as never;

beforeEach(() => {
  saveNew.mockClear();
  overwrite.mockClear();
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => '# Hello' } as Response)));
});

describe('TextResourcePreview', () => {
  it('fetches text and renders the markdown editor read-only', async () => {
    render(<TextResourcePreview resource={res()} fileUrl="http://x/file" canEdit={false} />);
    await waitFor(() => expect(screen.getByTestId('md-editor')).toHaveTextContent('# Hello'));
    expect(screen.getByTestId('md-editor').getAttribute('data-readonly')).toBe('true');
    // No edit button when canEdit is false.
    expect(screen.queryByText('Edit')).toBeNull();
  });

  it('renders the code editor for a .json resource', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => '{"a":1}' } as Response)));
    render(<TextResourcePreview resource={res({ filename: 'c.json', mime_type: 'application/json' })} fileUrl="http://x/file" canEdit={false} />);
    await waitFor(() => expect(screen.getByTestId('code-editor')).toBeInTheDocument());
  });

  it('editor + save-as-new-version wiring', async () => {
    render(<TextResourcePreview resource={res()} fileUrl="http://x/file" canEdit onSaved={() => {}} />);
    await waitFor(() => screen.getByTestId('md-editor'));
    fireEvent.click(screen.getByText('Edit'));
    fireEvent.click(screen.getByText('Save as new version'));
    await waitFor(() => expect(saveNew).toHaveBeenCalledTimes(1));
  });
});
