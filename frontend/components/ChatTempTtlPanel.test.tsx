import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ChatTempTtlPanel } from './ChatTempTtlPanel';
import { tempTtlService } from '../services/tempTtlService';

vi.mock('../services/tempTtlService', () => ({
  tempTtlService: {
    getChatTempTtl: vi.fn(),
    setChatTempTtl: vi.fn(),
  },
}));

describe('ChatTempTtlPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads and displays the current TTL on mount', async () => {
    (tempTtlService.getChatTempTtl as ReturnType<typeof vi.fn>).mockResolvedValue({
      ttl_days: 14,
    });
    render(<ChatTempTtlPanel scopeType="personal" scopeId="u1" />);
    await waitFor(() => {
      expect(screen.getByLabelText(/Chat attachment TTL/i)).toHaveValue('14');
    });
  });

  it('persists a new selection via setChatTempTtl', async () => {
    (tempTtlService.getChatTempTtl as ReturnType<typeof vi.fn>).mockResolvedValue({
      ttl_days: 30,
    });
    (tempTtlService.setChatTempTtl as ReturnType<typeof vi.fn>).mockResolvedValue({
      ttl_days: 7,
    });
    render(<ChatTempTtlPanel scopeType="team" scopeId="42" />);
    const select = await screen.findByLabelText(/Chat attachment TTL/i);
    fireEvent.change(select, { target: { value: '7' } });
    await waitFor(() => {
      expect(tempTtlService.setChatTempTtl).toHaveBeenCalledWith('team', '42', 7);
    });
  });

  it('renders "Never" for ttl_days=-1', async () => {
    (tempTtlService.getChatTempTtl as ReturnType<typeof vi.fn>).mockResolvedValue({
      ttl_days: -1,
    });
    render(<ChatTempTtlPanel scopeType="personal" scopeId="u1" />);
    const select = await screen.findByLabelText(/Chat attachment TTL/i);
    await waitFor(() => {
      expect(select).toHaveValue('-1');
    });
  });

  it('surfaces a load error in the panel', async () => {
    (tempTtlService.getChatTempTtl as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('500: boom'),
    );
    render(<ChatTempTtlPanel scopeType="personal" scopeId="u1" />);
    await waitFor(() => {
      expect(screen.getByText(/boom/i)).toBeInTheDocument();
    });
  });

  it('shows the optional label text in the visible label', async () => {
    (tempTtlService.getChatTempTtl as ReturnType<typeof vi.fn>).mockResolvedValue({ ttl_days: 30 });
    render(<ChatTempTtlPanel scopeType="team" scopeId="42" label="Acme Corp" />);
    expect(await screen.findByText(/Acme Corp/i)).toBeInTheDocument();
  });
});
