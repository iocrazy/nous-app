/**
 * ⌘K 统一面（harness 三期 3c §2.5 / spec §6 稿一）。
 *
 * 五条：⌘K 打开、Esc 关；输入防抖 200 ms 后**一次**请求；↑↓ 在**组间连续**移动
 * （三组是一个列表，不是三个各自循环的列表——否则 Enter 会打开你没看着的那
 * 行）；空态 / 无命中 / 失败三种文案互不相同；`deep_link` 为空串的行 Enter 跳过
 * （无议题的个人 run 没有可跳转的页面，导航到空串会把整个 app 跳回根路由）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const v = (typeof fallback === 'object' && fallback ? fallback : vars) as
        | Record<string, unknown>
        | undefined;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_m, n: string) => String(v?.[n] ?? `{{${n}}}`));
    },
  }),
}));

const navigateMock = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});

const searchMock = vi.fn();
vi.mock('../../services/unifiedSearchService', async (importOriginal) => {
  // 部分 mock：只换掉网络那一层。`MIN_SEARCH_QUERY_CHARS` 是**契约里的数**（端点
  // 的 `MIN_QUERY_CHARS` 的镜像），在 mock 里再写一遍就等于让测试里的阈值和生产
  // 的阈值各自演化。
  const actual = await importOriginal<typeof import('../../services/unifiedSearchService')>();
  return { ...actual, unifiedSearch: (...a: unknown[]) => searchMock(...a) };
});

const { CommandPalette } = await import('./CommandPalette');
const { useCommandPalette } = await import('../../stores/commandPaletteStore');

const HIT = (kind: string, id: string, title: string, deepLink = '/team/7/todolist/MH-96') => ({
  kind,
  id,
  title,
  snippet: null,
  deep_link: deepLink,
  issue_key: 'MH-96',
  issue_id: '96',
  meta: {},
});
const RESULT = {
  groups: {
    issues: [HIT('issue', '96', 'MH-96 · Alpha rain on glass')],
    runs: [HIT('run', '913', 'MH-96 · Alpha')],
    // 产出命中的 id 是 `kind:ref_id:version`（契约补充），面板只拿它当 React key。
    outputs: [HIT('output', 'script_shot:9:4', 'S3 · Shot 1 · MS')],
  },
  totals: { issues: 1, runs: 1, outputs: 1 },
  took_ms: 9,
};
const EMPTY = {
  groups: { issues: [], runs: [], outputs: [] },
  totals: { issues: 0, runs: 0, outputs: 0 },
  took_ms: 3,
};

beforeEach(() => {
  vi.useFakeTimers();
  searchMock.mockReset();
  navigateMock.mockReset();
  // The store is module state — it outlives a test the way it outlives a route
  // change. Left open, every case after the first would start already showing.
  useCommandPalette.getState().setOpen(false);
});
afterEach(() => vi.useRealTimers());

const mount = () =>
  render(
    <MemoryRouter>
      <CommandPalette />
    </MemoryRouter>,
  );
const open = () => {
  act(() => {
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
  });
};
const type = (v: string) =>
  act(() => {
    fireEvent.change(screen.getByTestId('command-palette-input'), { target: { value: v } });
  });
const rows = () => screen.getAllByTestId('command-palette-row');

describe('CommandPalette', () => {
  it('opens on Cmd+K and closes on Escape', () => {
    mount();
    expect(screen.queryByTestId('command-palette')).toBeNull();
    open();
    expect(screen.getByTestId('command-palette')).toBeTruthy();
    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });
    expect(screen.queryByTestId('command-palette')).toBeNull();
  });

  it('debounces to one request 200 ms after the last keystroke', () => {
    searchMock.mockResolvedValue(RESULT);
    mount();
    open();
    type('ra');
    type('rain');
    act(() => {
      vi.advanceTimersByTime(199);
    });
    expect(searchMock).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(searchMock).toHaveBeenCalledTimes(1);
    expect(searchMock.mock.calls[0][0]).toMatchObject({ q: 'rain' });
  });

  it('moves the highlight across group boundaries', async () => {
    searchMock.mockResolvedValue(RESULT);
    mount();
    open();
    type('rain');
    await act(async () => {
      vi.advanceTimersByTime(200);
    });
    expect(rows()[0].getAttribute('data-active')).toBe('true');
    act(() => {
      fireEvent.keyDown(window, { key: 'ArrowDown' });
    });
    // 第二行属于**下一组**——三组是一个列表。
    expect(rows()[1].getAttribute('data-active')).toBe('true');
    expect(rows()[1].getAttribute('data-kind')).toBe('run');
    expect(rows()[0].getAttribute('data-active')).toBe('false');
    // 走到底再往下回到第一行：一个列表，一个环。
    act(() => {
      fireEvent.keyDown(window, { key: 'ArrowDown' });
      fireEvent.keyDown(window, { key: 'ArrowDown' });
    });
    expect(rows()[0].getAttribute('data-active')).toBe('true');
  });

  it('opens the highlighted row on Enter and closes', async () => {
    searchMock.mockResolvedValue(RESULT);
    mount();
    open();
    type('rain');
    await act(async () => {
      vi.advanceTimersByTime(200);
    });
    act(() => {
      fireEvent.keyDown(window, { key: 'Enter' });
    });
    expect(navigateMock).toHaveBeenCalledWith('/team/7/todolist/MH-96');
    expect(screen.queryByTestId('command-palette')).toBeNull();
  });

  it('never navigates to a hit with no page behind it', async () => {
    // 无议题的个人 run：后端给空串，不是 null。`navigate('')` 不是 no-op——它把
    // 整个 app 跳回根路由，也就是「按回车，页面没了」。
    searchMock.mockResolvedValue({
      groups: { issues: [], runs: [HIT('run', '913', 'Alpha', '')], outputs: [] },
      totals: { issues: 0, runs: 1, outputs: 0 },
      took_ms: 4,
    });
    mount();
    open();
    type('alpha');
    await act(async () => {
      vi.advanceTimersByTime(200);
    });
    expect(rows()[0].getAttribute('data-linkless')).toBe('true');
    act(() => {
      fireEvent.keyDown(window, { key: 'Enter' });
    });
    expect(navigateMock).not.toHaveBeenCalled();
    // 面板留着——什么都没发生的回车不该看起来像成功。
    expect(screen.getByTestId('command-palette')).toBeTruthy();
  });

  it('tells an empty box, an empty result and a failure apart', async () => {
    mount();
    open();
    expect(screen.getByTestId('command-palette-hint')).toBeTruthy();

    searchMock.mockResolvedValue(EMPTY);
    type('zzz');
    await act(async () => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByTestId('command-palette-empty').textContent).toContain('zzz');

    searchMock.mockRejectedValue(Object.assign(new Error('nope'), { code: 'query_too_short' }));
    type('qq');
    await act(async () => {
      vi.advanceTimersByTime(200);
    });
    // 「搜不到」与「搜不成」是读者会采取不同行动的两个答案。
    expect(screen.getByTestId('command-palette-error').textContent).toContain('query_too_short');
    expect(screen.queryByTestId('command-palette-empty')).toBeNull();
  });
});

/**
 * 面板作为「第二个监听者」的义务（3c Task 17 修复轮 1）。
 *
 * ⌘K 在画布路由下有两个监听者。裁定是**画布赢**：画布内的 ⌘K 是既有功能。
 * 画布在 capture 阶段认领并 `preventDefault()`，面板这一侧的义务就是——不抢
 * 已经被处理过的按键。
 */
describe('CommandPalette — 不抢别人已经处理过的按键', () => {
  it('capture 阶段有人认领了 ⌘K，面板不打开', () => {
    const owner = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) e.preventDefault();
    };
    window.addEventListener('keydown', owner, true);
    try {
      mount();
      open();
      expect(screen.queryByTestId('command-palette')).toBeNull();
    } finally {
      window.removeEventListener('keydown', owner, true);
    }
  });

  it('Escape 只关这一层 —— 不让外面的 handler 跟着关', () => {
    // 全仓其他 Escape handler 不判 `defaultPrevented`，所以光 preventDefault
    // 拦不住它们：一次 Escape 会同时关掉面板和它背后的对话框。
    const outer = vi.fn();
    window.addEventListener('keydown', outer);
    try {
      mount();
      open();
      expect(screen.getByTestId('command-palette')).toBeTruthy();
      outer.mockClear();
      act(() => {
        fireEvent.keyDown(window, { key: 'Escape' });
      });
      expect(screen.queryByTestId('command-palette')).toBeNull();
      expect(outer).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('keydown', outer);
    }
  });
});

/**
 * 可访问性（3c Task 17 修复轮 1）。
 *
 * 一个用键盘开、用键盘选的面板，是屏幕阅读器用户最依赖也最容易被落下的那种
 * 控件：高亮只存在于一个 class 里，读屏软件无从知道「现在停在哪一行」。
 */
describe('CommandPalette — 可访问性', () => {
  it('把高亮说成 aria-activedescendant，而不只是一个背景色', async () => {
    searchMock.mockResolvedValue(RESULT);
    mount();
    open();
    type('rain');
    await act(async () => {
      vi.advanceTimersByTime(200);
    });
    const input = screen.getByTestId('command-palette-input');
    const list = screen.getByRole('listbox');
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(3);
    expect(list).toBeTruthy();
    expect(input.getAttribute('aria-activedescendant')).toBe(options[0].id);
    act(() => {
      fireEvent.keyDown(window, { key: 'ArrowDown' });
    });
    expect(input.getAttribute('aria-activedescendant')).toBe(screen.getAllByRole('option')[1].id);
    expect(screen.getAllByRole('option')[1].getAttribute('aria-selected')).toBe('true');
  });

  it('关闭后焦点回到打开它的那个元素', () => {
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();
    try {
      mount();
      open();
      // 面板拿走焦点是对的 —— 它是一个 modal。
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(document.activeElement).toBe(screen.getByTestId('command-palette-input'));
      act(() => {
        fireEvent.keyDown(window, { key: 'Escape' });
      });
      // 关掉之后不还回去，读者的键盘位置就丢在 body 上了。
      expect(document.activeElement).toBe(trigger);
    } finally {
      trigger.remove();
    }
  });

  it('Tab 在面板内循环，不会把焦点丢到背后的页面上', async () => {
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    searchMock.mockResolvedValue(RESULT);
    try {
      mount();
      open();
      type('rain');
      await act(async () => {
        vi.advanceTimersByTime(200);
      });
      const input = screen.getByTestId('command-palette-input');
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(document.activeElement).toBe(input);
      // 输入框 → 三个结果行，每一步都还在面板里。
      const rowEls = rows();
      act(() => {
        fireEvent.keyDown(window, { key: 'Tab' });
      });
      expect(document.activeElement).toBe(rowEls[0]);
      act(() => {
        fireEvent.keyDown(window, { key: 'Tab' });
        fireEvent.keyDown(window, { key: 'Tab' });
      });
      expect(document.activeElement).toBe(rowEls[2]);
      // 最后一个再 Tab 回到开头，而不是落到面板外那个按钮上。
      act(() => {
        fireEvent.keyDown(window, { key: 'Tab' });
      });
      expect(document.activeElement).toBe(input);
      expect(document.activeElement).not.toBe(outside);
      // 反向同理。
      act(() => {
        fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
      });
      expect(document.activeElement).toBe(rowEls[2]);
    } finally {
      outside.remove();
    }
  });
});
