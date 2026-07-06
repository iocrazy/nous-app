/**
 * ShotCard tests (Phase B P3).
 *
 * Covers the four status corners, parameter-pill vocab cycling, the debounced
 * description PATCH, focal-length free-text commit, inline-confirm delete, and
 * the drag wiring the owning column hands each card.
 */
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../sceneService';
import { ShotCard, type ShotReorderApi } from '../storyboard/ShotCard';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const shot = (over: Partial<Shot> = {}): Shot => ({
  id: '900',
  scene_id: '200',
  shot_number: 1,
  shot_type: null,
  camera_angle: null,
  camera_movement: null,
  focal_length: null,
  lighting: null,
  description: null,
  image_url: null,
  thumbnail_url: null,
  video_url: null,
  status: 'empty',
  sort_order: 1000,
  ...over,
});

const noReorder = (): ShotReorderApi => ({
  isDragging: false,
  dropEdge: null,
  onDragStart: vi.fn(),
  onDragEnd: vi.fn(),
  onDragOver: vi.fn(),
  onDrop: vi.fn(),
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ShotCard — status corner', () => {
  it('renders a dash for an empty shot', () => {
    render(<ShotCard shot={shot({ status: 'empty' })} index={1} onUpdate={vi.fn()} onDelete={vi.fn()} reorder={noReorder()} />);
    expect(screen.getByLabelText('editor.shotStatusEmpty')).toBeInTheDocument();
  });

  it('renders a live status region while generating', () => {
    render(<ShotCard shot={shot({ status: 'generating' })} index={1} onUpdate={vi.fn()} onDelete={vi.fn()} reorder={noReorder()} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('renders the thumbnail when done', () => {
    render(
      <ShotCard
        shot={shot({ status: 'done', image_url: 'https://x/img.png', thumbnail_url: 'https://x/thumb.png' })}
        index={1}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
        reorder={noReorder()}
      />,
    );
    const img = screen.getByRole('img') as HTMLImageElement;
    // thumbnail_url wins over image_url when both are present.
    expect(img.src).toBe('https://x/thumb.png');
    expect(img.getAttribute('loading')).toBe('lazy');
  });

  it('renders a retry label when failed', () => {
    render(<ShotCard shot={shot({ status: 'failed' })} index={1} onUpdate={vi.fn()} onDelete={vi.fn()} reorder={noReorder()} />);
    expect(screen.getByText('editor.shotRetry')).toBeInTheDocument();
  });
});

describe('ShotCard — parameter pills', () => {
  it('cycles a vocabulary field from unset to its first value on click', () => {
    const onUpdate = vi.fn();
    render(<ShotCard shot={shot({ shot_type: null })} index={1} onUpdate={onUpdate} onDelete={vi.fn()} reorder={noReorder()} />);
    fireEvent.click(screen.getByTestId('shot-pill-shot_type'));
    expect(onUpdate).toHaveBeenCalledWith('900', { shot_type: 'WIDE' });
  });

  it('advances to the next vocabulary value and wraps', () => {
    const onUpdate = vi.fn();
    render(<ShotCard shot={shot({ camera_movement: 'HANDHELD' })} index={1} onUpdate={onUpdate} onDelete={vi.fn()} reorder={noReorder()} />);
    // HANDHELD is the last CAMERA_MOVEMENTS entry → wraps back to STATIC.
    fireEvent.click(screen.getByTestId('shot-pill-camera_movement'));
    expect(onUpdate).toHaveBeenCalledWith('900', { camera_movement: 'STATIC' });
  });

  it('commits focal length free-text on blur', () => {
    const onUpdate = vi.fn();
    render(<ShotCard shot={shot({ focal_length: null })} index={1} onUpdate={onUpdate} onDelete={vi.fn()} reorder={noReorder()} />);
    const focal = screen.getByTestId('shot-focal') as HTMLInputElement;
    fireEvent.change(focal, { target: { value: '85mm' } });
    fireEvent.blur(focal);
    expect(onUpdate).toHaveBeenCalledWith('900', { focal_length: '85mm' });
  });
});

describe('ShotCard — description debounce', () => {
  it('PATCHes the description once after the debounce window', () => {
    vi.useFakeTimers();
    const onUpdate = vi.fn();
    render(<ShotCard shot={shot({ description: 'old' })} index={1} onUpdate={onUpdate} onDelete={vi.fn()} reorder={noReorder()} />);
    const area = screen.getByTestId('shot-desc');
    fireEvent.change(area, { target: { value: 'a new blocking' } });
    expect(onUpdate).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(600));
    expect(onUpdate).toHaveBeenCalledWith('900', { description: 'a new blocking' });
    vi.useRealTimers();
  });
});

describe('ShotCard — inline-confirm delete', () => {
  it('arms on first click and deletes on the second', () => {
    const onDelete = vi.fn();
    render(<ShotCard shot={shot({})} index={1} onUpdate={vi.fn()} onDelete={onDelete} reorder={noReorder()} />);
    const del = screen.getByLabelText('editor.shotDelete');
    fireEvent.click(del);
    expect(onDelete).not.toHaveBeenCalled();
    expect(del).toHaveTextContent('editor.nodesConfirm');
    fireEvent.click(del);
    expect(onDelete).toHaveBeenCalledWith('900');
  });
});

describe('ShotCard — drag wiring', () => {
  it('starts a drag and reports a drop through the reorder api', () => {
    const reorder = noReorder();
    render(<ShotCard shot={shot({})} index={1} onUpdate={vi.fn()} onDelete={vi.fn()} reorder={reorder} />);
    const card = screen.getByTestId('shot-card');
    fireEvent.dragStart(card);
    expect(reorder.onDragStart).toHaveBeenCalled();
    fireEvent.drop(card);
    expect(reorder.onDrop).toHaveBeenCalled();
  });

  it('generate button is disabled without a handler', () => {
    render(<ShotCard shot={shot({})} index={1} onUpdate={vi.fn()} onDelete={vi.fn()} reorder={noReorder()} />);
    expect(screen.getByText('editor.shotGenerate')).toBeDisabled();
  });
});

describe('ShotCard — generate', () => {
  it('dispatches generate on click when wired', () => {
    const onGenerate = vi.fn();
    render(<ShotCard shot={shot({})} index={1} onUpdate={vi.fn()} onDelete={vi.fn()} onGenerate={onGenerate} reorder={noReorder()} />);
    fireEvent.click(screen.getByText('editor.shotGenerate'));
    expect(onGenerate).toHaveBeenCalledWith('900');
  });

  it('degrades to a disabled coming-soon button when the feature is off', () => {
    const onGenerate = vi.fn();
    render(
      <ShotCard
        shot={shot({})}
        index={1}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
        onGenerate={onGenerate}
        generateDisabled
        reorder={noReorder()}
      />,
    );
    const btn = screen.getByText('editor.shotGenerate');
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', 'editor.shotGenerateComingSoon');
    fireEvent.click(btn);
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it('reads Retry on a failed shot and re-dispatches on click', () => {
    const onGenerate = vi.fn();
    render(
      <ShotCard shot={shot({ status: 'failed' })} index={1} onUpdate={vi.fn()} onDelete={vi.fn()} onGenerate={onGenerate} reorder={noReorder()} />,
    );
    const retry = screen.getByText('editor.shotRetry');
    fireEvent.click(retry);
    expect(onGenerate).toHaveBeenCalledWith('900');
  });

  it('disables the button while generating', () => {
    render(
      <ShotCard shot={shot({ status: 'generating' })} index={1} onUpdate={vi.fn()} onDelete={vi.fn()} onGenerate={vi.fn()} reorder={noReorder()} />,
    );
    expect(screen.getByText('editor.shotGenerate')).toBeDisabled();
  });
});
