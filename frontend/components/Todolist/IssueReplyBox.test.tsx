import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { IssueReplyBox } from './IssueReplyBox';

// Mock the upload service to avoid hitting the network.
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    uploadChatAttachment: vi.fn().mockResolvedValue({
      kind: 'image',
      url: 'personal/u1/temp/x.png',
      filename: 'x.png',
      size_bytes: 100,
      mime: 'image/png',
      resource_id: 'res-1',
      file_path: 'personal/u1/temp/x.png',
    }),
  },
}));

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

const _agents = [{ id: 'a1', slug: 'agent-1', name: 'Agent 1' }];

describe('IssueReplyBox', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the attachment picker (paperclip button) when no chips', () => {
    render(
      <IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />,
    );
    // The picker renders a button with the Paperclip icon; we can find it by title or role.
    // The picker uses i18n key 'chat.attachments.attachTooltip' — accept either the key OR an English fallback.
    expect(
      screen.getByRole('button', { name: /attach|paperclip/i }),
    ).toBeInTheDocument();
  });

  it('passes attachments[] to onSubmit when send fires', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" onSubmit={onSubmit} />,
    );

    // Type a body
    const textarea = container.querySelector('textarea')!;
    fireEvent.change(textarea, { target: { value: 'hello' } });

    // Simulate a paste event with a file
    const file = new File(['data'], 'x.png', { type: 'image/png' });
    const clipboardData = {
      files: [file] as unknown as FileList,
      items: [] as unknown as DataTransferItemList,
      types: [],
      getData: () => '',
    };
    fireEvent.paste(textarea, { clipboardData });

    // Wait for the upload mock to resolve and the chip to appear
    await waitFor(() => {
      expect(screen.getByText(/x\.png/)).toBeInTheDocument();
    });

    // Click Send — find the submit button (likely a Send icon button)
    const sendBtn = container.querySelector('button[type="submit"], button[aria-label*="send" i]')!;
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
      const args = onSubmit.mock.calls[0];
      expect(args[0]).toBe('hello');            // body
      expect(args[1]).toBe('a1');                // agentId
      const atts = args[2];
      expect(Array.isArray(atts)).toBe(true);
      expect(atts).toHaveLength(1);
      expect(atts[0].kind).toBe('image');
      expect(atts[0].url).toBe('personal/u1/temp/x.png');
    });
  });

  it('shows the "Drop files to attach" overlay on drag enter', () => {
    const { container } = render(
      <IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />,
    );
    const wrapper = container.firstElementChild! as HTMLElement;
    fireEvent.dragEnter(wrapper);
    expect(screen.getByText(/Drop files to attach/i)).toBeInTheDocument();
  });

  it('clears chips after a successful send', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" onSubmit={onSubmit} />,
    );
    const textarea = container.querySelector('textarea')!;
    fireEvent.change(textarea, { target: { value: 'msg' } });

    const file = new File(['data'], 'x.png', { type: 'image/png' });
    fireEvent.paste(textarea, {
      clipboardData: { files: [file] as unknown as FileList },
    });
    await waitFor(() => expect(screen.getByText(/x\.png/)).toBeInTheDocument());

    const sendBtn = container.querySelector('button[type="submit"], button[aria-label*="send" i]')!;
    fireEvent.click(sendBtn);
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(/x\.png/)).not.toBeInTheDocument());
  });
});
