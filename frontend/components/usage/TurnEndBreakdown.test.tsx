/** turn_end 分布条（3c §6 稿三）。十个 reason 里只有五个值得一眼分辨，其余归 Other。 */
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TurnEndBreakdown } from './TurnEndBreakdown';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, f?: string) => f ?? k }) }));
afterEach(cleanup);

describe('TurnEndBreakdown', () => {
  it('renders one segment per reason sized by its share', () => {
    render(<TurnEndBreakdown reasons={{ completed: 8, error: 1, interrupted: 1 }} />);
    expect(screen.getByTestId('turn-end-completed')).toHaveStyle({ width: '80%' });
    expect(screen.getByTestId('turn-end-error')).toHaveStyle({ width: '10%' });
  });

  it('folds unknown reasons into one Other segment', () => {
    render(<TurnEndBreakdown reasons={{ completed: 2, max_iterations: 1, paused: 1 }} />);
    expect(screen.getByTestId('turn-end-other')).toHaveStyle({ width: '50%' });
  });

  it('says so when nothing has ended yet instead of drawing an empty bar', () => {
    // 空条与「全是 completed 的条」是两回事，不能长得一样。
    render(<TurnEndBreakdown reasons={{}} />);
    expect(screen.getByTestId('turn-end-empty')).toHaveTextContent('No finished runs yet');
    expect(screen.queryByTestId('turn-end-completed')).toBeNull();
  });

  it('paints each reason with the semantic token its meaning earns, not a hue', () => {
    // K1 之后色相类名不再表示状态；awaiting_input 是「在等人」(info)，
    // 不是失败(danger)——这条断言就是分布条与 Success tile 的同一个裁定。
    render(<TurnEndBreakdown reasons={{ completed: 1, awaiting_input: 1, error: 1, interrupted: 1, cancelled: 1 }} />);
    expect(screen.getByTestId('turn-end-completed').className).toContain('bg-ok');
    expect(screen.getByTestId('turn-end-awaiting_input').className).toContain('bg-info');
    expect(screen.getByTestId('turn-end-error').className).toContain('bg-danger');
    expect(screen.getByTestId('turn-end-interrupted').className).toContain('bg-warn');
    // cancelled 与 Other 同属 muted：它们都不是需要一眼分辨的状态。
    expect(screen.getByTestId('turn-end-cancelled').className).toContain('bg-ink-');
  });

  it('omits a reason nobody hit rather than drawing a zero-width sliver', () => {
    render(<TurnEndBreakdown reasons={{ completed: 3 }} />);
    expect(screen.queryByTestId('turn-end-error')).toBeNull();
    expect(screen.queryByTestId('turn-end-other')).toBeNull();
    expect(screen.getByTestId('turn-end-completed')).toHaveStyle({ width: '100%' });
  });
});
