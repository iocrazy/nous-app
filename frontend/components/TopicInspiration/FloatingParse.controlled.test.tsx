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

  it('does not render the floating trigger button when controlled (open=false) — the parent supplies the entry point', () => {
    render(<FloatingParse open={false} onOpenChange={vi.fn()} />);
    // Collapsed-phase floating button renders "Parse Link" text with no other
    // markup; in controlled mode it must be entirely absent from the DOM so
    // the page doesn't show two redundant Parse entry points (top bar +
    // floating pill).
    expect(screen.queryByRole('button', { name: /parse link/i })).toBeNull();
  });

  it('prefills the input with initialUrl when opened controlled', () => {
    render(<FloatingParse open onOpenChange={vi.fn()} initialUrl="https://douyin.com/x" />);
    expect(screen.getByDisplayValue('https://douyin.com/x')).toBeTruthy();
  });
});
