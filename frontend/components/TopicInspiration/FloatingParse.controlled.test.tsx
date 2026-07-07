import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f?: string) => f ?? _k }),
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../../services/parserService', () => ({
  parseShareLink: vi.fn(),
  parseBatchLinks: vi.fn(),
  getSodaPlaylist: vi.fn(),
  downloadSodaTracks: vi.fn(),
}));
vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn(),
}));

import { FloatingParse } from './FloatingParse';

describe('FloatingParse controlled open', () => {
  it('renders the input panel when open=true is passed', () => {
    render(<FloatingParse open onOpenChange={vi.fn()} />);
    // 展开态含输入框 placeholder(与 collapsed 的圆按钮区分)
    expect(screen.getByPlaceholderText(/paste/i)).toBeTruthy();
  });

  it('stays collapsed when open=false', () => {
    render(<FloatingParse open={false} onOpenChange={vi.fn()} />);
    expect(screen.queryByPlaceholderText(/paste/i)).toBeNull();
  });
});
