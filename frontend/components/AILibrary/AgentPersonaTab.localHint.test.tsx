/**
 * AgentPersonaTab — 本机模型的纯文本提示。
 *
 * "Codex (Local)" 跑在用户自己电脑的 daemon 上,链路是纯文本:没有 tool calling,
 * 所以这个 agent 绑定的 Skill 与工具在运行时会被后端直接拒绝
 * (`local_tools_unsupported`)。在选中模型的那一刻说清楚,比让用户发一条消息才
 * 撞上错误码要早得多——这是"触发路径必须类型化失败回显"的预防侧。
 *
 * 提示只在"选了本机模型 **且** 真的绑了 Skill"时出现:两者缺一,就没有会被拒绝
 * 的东西,一条常驻警告只会训练用户忽略它。
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AILibraryAgent, AILibrarySkill } from '../../types';
import en from '../../public/locales/en.json';

vi.mock('./MarkdownEditor', () => ({ MarkdownEditor: () => <div /> }));
vi.mock('./AgentIconPicker', () => ({ AgentIconPicker: () => <div /> }));
vi.mock('./agentEditorModel', () => ({ renderModelSelect: () => <div /> }));

// 先查真实的 en.json,查不到才退回组件自带的 default 字符串。这样断言的是**发货
// 的英文文案**而不是测试里手写的一张表:漏加 key 会在这里红,而不是让用户看到
// 空白。(范式来自 components/AISettings.modelHealth.test.tsx。)
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, second?: unknown, third?: unknown): string => {
      const fromLocale = key
        .split('.')
        .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
      const template =
        typeof fromLocale === 'string'
          ? fromLocale
          : typeof second === 'string'
            ? second
            : '';
      const vars = ((typeof second === 'object' ? second : third) ?? {}) as Record<
        string,
        unknown
      >;
      return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars[name] ?? ''));
    },
  }),
  // AgentPersonaTab -> utils/relativeTime -> utils/formatDate -> i18n.ts 在模块求值
  // 期就调用 i18n.use(initReactI18next),这个具名导出缺了会在任何测试跑之前抛错。
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

import { AgentPersonaTab } from './AgentPersonaTab';

const agent = { id: 'a1', slug: 'script-ai', name: 'Script AI' } as AILibraryAgent;
const ALL = [
  { id: 1, name: 'Outline', slug: 's-1', is_public: false, team_id: null, project_id: null },
] as AILibrarySkill[];

const HINT =
  'Local Codex runs as plain text: Skills and tools bound to this agent will be rejected at run time.';

function tree(over: Partial<React.ComponentProps<typeof AgentPersonaTab>> = {}) {
  return (
    <MemoryRouter>
      <AgentPersonaTab
        agent={agent}
        draft={{}}
        updateDraft={vi.fn()}
        readOnly={false}
        catalogLocked={false}
        modelGroups={[]}
        localModelNames={['Codex (Local)']}
        localSkillIds={[1]}
        allSkills={ALL}
        skillsLoading={false}
        onAddSkill={vi.fn()}
        onRemoveSkill={vi.fn()}
        onMoveSkill={vi.fn()}
        {...over}
      />
    </MemoryRouter>
  );
}

describe('AgentPersonaTab — 本机模型纯文本提示', () => {
  it('shows the plain-text hint only when a local model is selected AND skills are bound', () => {
    const { rerender } = render(tree({ draft: { model: 'Codex (Local)' }, localSkillIds: [1] }));
    expect(screen.getByText(HINT)).toBeInTheDocument();

    // 绑定为空:没有会被拒绝的东西,一条常驻警告只会训练用户忽略它。
    rerender(tree({ draft: { model: 'Codex (Local)' }, localSkillIds: [] }));
    expect(screen.queryByText(/plain text/)).toBeNull();

    // 换成平台模型:工具调用正常,提示必须消失。
    rerender(tree({ draft: { model: 'qwen-max' }, localSkillIds: [1] }));
    expect(screen.queryByText(/plain text/)).toBeNull();
  });

  it('是本机模型才提示 —— 名字不在 localModelNames 里的不算', () => {
    // localModelNames 由后端 is_local 派生,不是前端按名字猜的。一个恰好也叫
    // "Codex (Local)" 但后端没标 is_local 的行不该触发提示。
    render(tree({ draft: { model: 'Codex (Local)' }, localModelNames: [], localSkillIds: [1] }));
    expect(screen.queryByTestId('local-model-plain-text-hint')).toBeNull();
  });

  it('未选任何模型时保持安静', () => {
    render(tree({ draft: {}, localSkillIds: [1] }));
    expect(screen.queryByTestId('local-model-plain-text-hint')).toBeNull();
  });
});
