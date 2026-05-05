/**
 * Mock data for the paperclip-style /todolist UI scaffold (A8).
 * Replaced by real data via issuesService + issue_messages once UI is locked.
 */

import type { Issue, IssueMessage, AgentRef, ProjectRef } from './types';

const MEDIAHUB_TEAM_ID = '300857142684210';

export const MOCK_AGENTS: Record<string, AgentRef> = {
  ceo: { id: 'agent-ceo', slug: 'ceo', name: 'CEO', avatar_color: 'bg-amber-500' },
  board: { id: 'agent-board', slug: 'board', name: 'Board', avatar_color: 'bg-purple-500' },
  parser: { id: 'agent-parser', slug: 'parser', name: 'Parser', avatar_color: 'bg-blue-500' },
  curator: { id: 'agent-curator', slug: 'curator', name: 'Curator', avatar_color: 'bg-emerald-500' },
};

export const MOCK_PROJECTS: Record<string, ProjectRef> = {
  onboarding: { id: 'proj-onboarding', name: 'Onboarding', color: 'bg-violet-500' },
  content: { id: 'proj-content', name: 'Content', color: 'bg-sky-500' },
};

export const MOCK_ISSUES: Issue[] = [
  {
    id: 'iss-1',
    identifier: 'MED-1',
    title: 'Hire your first engineer and create a hiring plan',
    description: '招聘第一位工程师，制定 hiring plan。Hiring plan 应当包含 sourcing / interview pipeline / leveling rubric / first-90-days OKRs。',
    status: 'blocked',
    priority: 'no_priority',
    project: MOCK_PROJECTS.onboarding,
    assignee: MOCK_AGENTS.ceo,
    parent_id: null,
    created_at: '2026-04-26T10:00:00Z',
    updated_at: '2026-04-28T10:00:00Z',
    last_activity_at: '2026-04-28T10:00:00Z',
    team_id: MEDIAHUB_TEAM_ID,
  },
  {
    id: 'iss-2',
    identifier: 'MED-2',
    title: 'Propose initial tech stack and scaffold development environment',
    description: '梳理初期技术栈选型并搭建开发环境（不含 prod infra）。',
    status: 'blocked',
    priority: 'high',
    project: MOCK_PROJECTS.onboarding,
    assignee: MOCK_AGENTS.ceo,
    parent_id: null,
    created_at: '2026-04-26T11:00:00Z',
    updated_at: '2026-04-28T15:00:00Z',
    last_activity_at: '2026-04-28T15:00:00Z',
    team_id: MEDIAHUB_TEAM_ID,
  },
  {
    id: 'iss-3',
    identifier: 'MED-3',
    title: '编写看海的短文',
    description: '要有感情',
    status: 'done',
    priority: 'medium',
    project: MOCK_PROJECTS.onboarding,
    assignee: MOCK_AGENTS.ceo,
    parent_id: null,
    created_at: '2026-04-26T12:00:00Z',
    updated_at: '2026-04-26T14:25:00Z',
    last_activity_at: '2026-04-26T14:25:00Z',
    team_id: MEDIAHUB_TEAM_ID,
  },
  {
    id: 'iss-4',
    identifier: 'MED-4',
    title: 'Set up parsing pipeline for Douyin links',
    description: '抖音链接解析 pipeline：parse → resolve → fetch metadata → emit task_tracking row。',
    status: 'in_progress',
    priority: 'urgent',
    project: MOCK_PROJECTS.content,
    assignee: MOCK_AGENTS.parser,
    parent_id: null,
    created_at: '2026-05-01T09:00:00Z',
    updated_at: '2026-05-04T18:00:00Z',
    last_activity_at: '2026-05-04T18:00:00Z',
    team_id: MEDIAHUB_TEAM_ID,
  },
  {
    id: 'iss-5',
    identifier: 'MED-5',
    title: 'Curate weekly digest of new resources',
    description: '每周整理新资源摘要 → 发送给团队。',
    status: 'todo',
    priority: 'low',
    project: MOCK_PROJECTS.content,
    assignee: MOCK_AGENTS.curator,
    parent_id: null,
    created_at: '2026-05-03T10:00:00Z',
    updated_at: '2026-05-03T10:00:00Z',
    last_activity_at: '2026-05-03T10:00:00Z',
    team_id: MEDIAHUB_TEAM_ID,
  },
];

export const MOCK_MESSAGES: Record<string, IssueMessage[]> = {
  'iss-3': [
    {
      id: 'msg-3-1',
      issue_id: 'iss-3',
      kind: 'agent_run',
      author: MOCK_AGENTS.ceo,
      duration_seconds: 180,
      body:
`**交付：看海短文**

我作为 CEO 临时承担了这次创作（团队当前没有内容/市场方向的报告人）。
任务规模较小，按 "hire slow" 原则，未为单次任务发起永久 hire。
若后续仍有内容、市场、品牌类工作，我会发起 Content / CMO 招聘请求。

---

# 看海

风从很远的地方吹来，带着咸味，也带着某种说不清的辽远。

我又一次站在了海边。

海水一寸一寸地漫过沙，把昨夜的脚印一遍一遍地擦掉。它从不留情，也从不解释——这大概是它教我的第一件事：很多东西，本来就不必带走。

我望着远方，那里没有什么，可我看了很久。

潮声里夹着许多人没说完的话——有归人的、有离人的、有写给谁却没寄出的。海一一收下，从不回应，只用它最平稳的呼吸告诉你：是的，我都听见了。

太阳一寸一寸沉下去，把海染成铜色，又染成藏青。光斑碎在浪尖上，像一些走散了的故人，远远地，还在朝我招手。

我想哭，却没哭。

那一刻我忽然明白——

我必往后走。`,
      meta: { board_signoff: 'BO' },
      created_at: '2026-04-26T13:50:00Z',
    },
    {
      id: 'msg-3-2',
      issue_id: 'iss-3',
      kind: 'system_status',
      from_status: 'done',
      to_status: 'todo',
      author: { kind: 'user', id: 'sys', name: 'System' },
      created_at: '2026-04-26T14:10:00Z',
    },
    {
      id: 'msg-3-3',
      issue_id: 'iss-3',
      kind: 'agent_run',
      author: MOCK_AGENTS.ceo,
      duration_seconds: 35,
      body:
`感谢 board 的审阅与确认。关于"是否发起 Content Lead / CMO 招聘请求"，我的决定是 **暂不发起**，理由如下：

**为什么暂缓**

- **Founding phase 信号不足**：目前公司仍在创立期，[MED-1](#MED-1) onboarding 进行中，第一位工程师尚在 board 审批中。Content/CMO 是一个独立 function，没有清晰的产品/市场叙事之前招进来会闲置。
- **一次性任务不触发永久 hire**：单篇创作不构成"招一个全职"的依据。按 "hire slow / fire fast" 原则，需要看到 **持续 3+ 类相似任务** 或一个明确的市场叙事任务才考虑。
- **生成式创作我可以兜底**：在公司极小阶段，CEO 自己处理一次性的 brand/创作任务比维护一个未充分利用的 function 更划算。
- **每次招聘应解锁下一次**：现在解锁的应该是工程产出（产品本体），而不是市场放大器——还没有放大对象。

**触发条件（什么时候我会重提这个 hire）**

下面任一条件成立时，我会主动发起 Content Lead / CMO 招聘请求：

1. 一个月内出现 ≥ 3 条独立的内容/市场/品牌类 issue。
2. 产品进入需要对外讲故事的阶段（landing page、launch、PR、社区运营）。
3. board 直接指示提前启动。

**当前承诺**

- 此类一次性创作任务由我（CEO）继续兜底，不影响其他战略工作。
- 我会跟踪此类任务的频次，达到触发条件即提交 hire 请求。

任务交付已被 board 接受，关闭此 issue。`,
      created_at: '2026-04-26T14:21:00Z',
    },
    {
      id: 'msg-3-4',
      issue_id: 'iss-3',
      kind: 'system_status',
      from_status: 'in_progress',
      to_status: 'done',
      author: { kind: 'user', id: 'self', name: 'You' },
      created_at: '2026-04-26T14:21:30Z',
    },
    {
      id: 'msg-3-5',
      issue_id: 'iss-3',
      kind: 'agent_run',
      author: MOCK_AGENTS.ceo,
      body: 'MED-3 → done confirmed (`completedAt: 2026-04-26T14:21:16.397Z`). Wake was self-triggered by my own prior comment; no new external input to address. Exiting heartbeat.',
      created_at: '2026-04-26T14:25:00Z',
    },
  ],
  'iss-4': [
    {
      id: 'msg-4-1',
      issue_id: 'iss-4',
      kind: 'agent_run',
      author: MOCK_AGENTS.parser,
      duration_seconds: 12,
      body: 'Picked up issue. Initial pass: existing parsing chain in `media_router.py` covers v.douyin.com short links via the `lightweight` adapter. Will draft a state-machine diagram for the new `parse → resolve → fetch metadata` pipeline and post it next.',
      created_at: '2026-05-04T17:02:00Z',
    },
    {
      id: 'msg-4-2',
      issue_id: 'iss-4',
      kind: 'system_status',
      from_status: 'todo',
      to_status: 'in_progress',
      author: { kind: 'user', id: 'sys', name: 'System' },
      created_at: '2026-05-04T17:02:05Z',
    },
  ],
};

export function findIssueByIdentifier(identifier: string): Issue | undefined {
  return MOCK_ISSUES.find((i) => i.identifier === identifier);
}

export function getMessages(issueId: string): IssueMessage[] {
  return MOCK_MESSAGES[issueId] ?? [];
}
