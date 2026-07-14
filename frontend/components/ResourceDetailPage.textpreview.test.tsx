// frontend/components/ResourceDetailPage.textpreview.test.tsx
// Narrow test: FilePreview routes a text resource to TextResourcePreview
// instead of the "no preview" fallback. We import FilePreview via a tiny
// re-export to keep the test focused (see Step 3 note).
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('./resources/TextResourcePreview', () => ({
  TextResourcePreview: () => <div data-testid="text-preview" />,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }) }));
vi.mock('./Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

import { FilePreview } from './resources/FilePreview';

const res = (over: Record<string, unknown> = {}) => ({
  id: '10', filename: 'notes.md', mime_type: 'text/markdown',
  file_size_bytes: 20, source_type: 'upload', creator_id: 'u1', ...over,
}) as never;

describe('FilePreview text routing', () => {
  it('routes a markdown resource to TextResourcePreview', () => {
    render(<FilePreview resource={res()} fileUrl="http://x/file" currentUserId="u1" />);
    expect(screen.getByTestId('text-preview')).toBeInTheDocument();
  });
  it('still shows no-preview for an unknown binary', () => {
    render(<FilePreview resource={res({ filename: 'a.bin', mime_type: 'application/octet-stream' })} fileUrl="http://x/file" currentUserId="u1" />);
    expect(screen.queryByTestId('text-preview')).toBeNull();
  });
});
