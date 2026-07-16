# Issue 评论触发 chip + suppress — 设计方案

**日期**：2026-07-15
**分支**：`feat/issue-comment-trigger-chip`（基于 master v0.25.363）
**来源**：Issues 重设计方案第四波 follow-up（`project_issues_multica_redesign` 记忆）
**参考实现**：multica `packages/views/issues/components/comment-trigger-chips.tsx`

---

## 1. 要解决的问题

**prod 上正在发生**：`issue_messages_router.py:241` 的分支判断只有一句 `if assignee_agent_id:` —— 指派了 agent 的 issue 上，**任何评论都无条件启动一轮计费 agent turn**，UI 零提示。用户以为在记笔记，实际按下 ⌘↩ 就启动了付费任务。

两个缺口：

1. **不知情**：没有任何信号告诉用户这条评论会唤醒谁。
2. **无处可逃**：agent-assigned issue 上**没有任何方式**留一条不花钱的笔记（`issue_messages` 的 INSERT 只在无 agent 时才走到）。

#1400 的 run-confirm 确认门**只盖住了 `POST /issues/{id}/dispatch`**。评论是第二个、未设防的 agent 启动入口。

## 2. 不做什么（明确划界）

- **不加新守卫**。评论在 `done` 状态的 issue 上照样唤醒 agent —— 这与 multica 的刻意设计一致（`issue.go:2770`：*"comments are conversational and can happen at any stage, including after completion"*）。本方案只做**披露 + 可选抑制**，不改变默认触发语义。
- **不做 `/note` 前缀**。它只是 suppress 的另一种输入方式（键盘流），后端能力共用，可后续单独加。
- **不做 multica 的多 agent 设施**：头像堆叠、`+N` 溢出、popover 逐个勾选、来源标签（assignee/@mention/squad）、squad 自触发抑制、per-agent 在线状态。**MediaHub 一条 issue 最多一个 `assignee_agent_id`，没有 @提及 agent、没有 squad**，这些全部无指称对象。
- **不删 `payload.agent_id` 字段**。它是 vestigial（见 §7），删除属于 API 契约变更，另案。

## 3. 核心设计决策

### 3.1 不能复用 `dispatch-preview`（关键）

#1400 的 `GET /issues/{id}/dispatch-preview`（`issues_router.py:207`）镜像的是 **`dispatch_issue` 的守卫**：`dbos_disabled` / `terminal_status` / `no_assignee` / `already_running`。

**评论路径的判据完全不同** —— 它只看 `assignee_agent_id`，不看状态、不看运行中的锁。具体分歧：

| 场景 | dispatch-preview 说 | 评论路径实际 |
|---|---|---|
| issue 状态 `done` | `will_start=false`（terminal_status） | **照样唤醒** |
| 有 live workflow 在跑 | `will_start=false`（already_running） | **照样唤醒** |

拿它渲染评论 chip = **在界面上撒谎**，比没有提示更坏。故新建独立端点。

### 3.2 判据抽成共用函数（比 #1400 更严）

`dispatch_preview` 是**手写镜像** `dispatch_issue` 的守卫（docstring 自陈 *"Mirrors dispatch_issue's own guards"`），这本身是漂移风险 —— #1400 接受了它。

**本方案不重复这个妥协**。抄 multica 的做法（`PreviewCommentTriggers` 和 `CreateComment` 调用同一个 `computeCommentAgentTriggers`）：

```python
# backend/app/services/issues/comment_trigger.py  (新文件)

@dataclass(frozen=True)
class CommentTriggerVerdict:
    will_wake: bool
    agent_id: Optional[str]

def compute_comment_trigger(issue_row: dict) -> CommentTriggerVerdict:
    """评论路径唯一的触发判据。POST 和 preview 端点共用。

    今天的规则就是一句 `if assignee_agent_id`（issue_messages_router:241 的原样搬迁），
    但从此它有唯一的家：判据一旦演进，两个调用方同步演进，不可能漂移。
    """
    agent_raw = issue_row.get("assignee_agent_id")
    if not agent_raw:
        return CommentTriggerVerdict(will_wake=False, agent_id=None)
    return CommentTriggerVerdict(will_wake=True, agent_id=str(agent_raw))
```

`post_issue_message` 与 preview 端点**都**通过它做决策。POST 里现存的 `if assignee_agent_id:` 改为 `if verdict.will_wake:`。

### 3.3 客户端只能做减法

POST body 新增 `suppress_agent_ids: list[str] | None`（**排除名单**，非开关）。服务端流程：

```
verdict = compute_comment_trigger(issue_row)      # 服务端算出会唤醒谁
if verdict.will_wake and verdict.agent_id in (suppress_agent_ids or []):
    → 走 note 路径（不 dispatch）
elif verdict.will_wake:
    → 走 dispatch 路径（今天的行为，不变）
else:
    → legacy issue_messages insert（今天的行为，不变）
```

**客户端永远无法凭空「加」一个触发**，只能从服务端自己算出的结果里减。这是 multica `filterSuppressedCommentAgentTriggers`（`comment.go:1446`）的语义。

**为何用 ID 列表而非 bool**（MediaHub 最多一个 agent，bool 也够）：preview 与 send 之间指派人若变更（A→B），ID 列表天然 no-op（B 不在名单里 → B 被唤醒，符合服务端真相）；bool 会静默抑制掉用户从没见过的 B。且 ID 列表与 multica 形状一致，未来加 @提及时无需改契约。

### 3.4 suppress 路径 = `run_session_turn` 减去模型调用

session 路径**今天的人类消息本来就是 `run_session_turn` 内部调 `append_user_message` 存的**（`ai_library_chat_service.py:429`）。所以 suppress 路径原样做它做的事，只是不调模型：

```python
await ConversationsAiStore().append_user_message(
    session_id=int(session_id),
    user_id=str(owner_id),
    content=payload.body,
    attachments=_display_attachments(payload.attachments),
)
```

**agent 下次跑 turn 一定看得到这条笔记** —— 历史加载是
`SELECT ... FROM public.messages WHERE conversation_id=:cid AND deleted_at IS NULL ORDER BY seq ASC`（`conversations_ai_store.py:462`），**无 role / trigger 过滤**。零 schema 变更。

**⚠️ attachments 必须用与 `run_session_turn` 相同的过滤**（`ai_library_chat_service.py:433-437`：只留 `kind/resource_id/mime/alt_text/name`），否则「留笔记」与「正常发」存进 `body['attachments']` 的形状不一致，history 重载渲染会分叉。抽 `_display_attachments()` 共用。

### 3.5 时间线可见性：白拿的

suppress 的笔记**自动出现在时间线上**，因为 GET 的 session 路径读的就是同一批 `public.messages` 行（`store.get_messages` + `map_ai_message_to_issue_message`）。无需任何前端改动。

**⚠️ 绝不能往 `issue_messages` 插这条笔记**：agent-assigned issue 的 GET **根本不查那张表**（`issue_messages_router.py:129` 是排他双路径，非 UNION），而前端 `IssueDetailView.tsx:148-160` 又订阅了 `issue_messages` 的 Realtime → 插进去会变成**「闪现一下、刷新就消失」的幻影行**（该文件 174-179 行已有针对同类 bug 的注释）。

### 3.6 无 agent 的 issue

`compute_comment_trigger` 返回 `will_wake=False` → 走今天的 legacy `issue_messages` insert，行为不变。`suppress_agent_ids` 在此路径是 no-op（没有触发可减）。chip 不渲染。

## 4. 端点契约

```
GET /api/v1/issues/{issue_id}/comment-trigger-preview
→ 200 CommentTriggerPreview { will_wake: bool, agent_id: str | null }
→ 404 若 issue 不可见
```

**纯读、零副作用**，与 `dispatch-preview` 同构。不接受草稿内容 —— 判据不看正文（MediaHub 无 @提及解析），故无需 POST、无需 debounce 请求正文。

> 与 multica 的差异：multica 的 preview 是 `POST` 且带 `content`，因为它要解析 @提及和 `/note`。MediaHub 判据只看 issue 行 → `GET` 足够，且可随 issue 数据一起缓存。加 `/note` 或 @提及时**必须**改成带正文的 POST。

## 5. 前端

**flag**：`VITE_FEATURE_ISSUE_COMMENT_TRIGGER` —— **与 `VITE_FEATURE_ISSUE_RUN_CONFIRM` 分开**。理由：run-confirm 至今 flag-dark 未上线；复用会把 chip 一起摁在暗处，而 chip 是止血项，应能独立 go-live。

**组件**：`IssueCommentTriggerChip.tsx`（约 40 行），渲染在 `IssueReplyBox` 编辑器上方。

| 状态 | 文案 | 视觉 |
|---|---|---|
| 会唤醒 | `Will start when sent · {agentName}` | 琥珀点 + ink-300 |
| 已抑制 | `Won't start this time · Click to restore` | 灰度 + `opacity-60` |

- 渲染条件：flag on && `preview.will_wake` && 草稿非空（对齐 multica `shouldRenderComposerHandoffPreview`）
- `aria-pressed={suppressed}`，`<button>` 语义
- agent 名由调用方 `agentsById` resolve —— **端点保持纯判据**，与 #1400 的 `DispatchConfirmDialog` 同范式
- suppress 是 **per-comment 一次性 React state**，提交后重置、切换 issue 时重置（multica `comment-input.tsx:82-92,132`）。**无持久化偏好**

**数据流**：`IssueDetailView` 取 preview（issue 变化时）→ 传给 `IssueReplyBox` → `submit()` 把 suppress 状态并入 `onSubmit` → `postIssueMessage` payload 加 `suppress_agent_ids`。

## 6. 测试

**后端**（pytest）
1. `compute_comment_trigger` 有 agent → `will_wake=True, agent_id=str`
2. `compute_comment_trigger` 无 agent → `will_wake=False, agent_id=None`
3. `compute_comment_trigger` agent_id 是 Snowflake → **返回 str 不数字化**（精度）
4. POST 无 suppress + 有 agent → dispatch 被调用（今天行为不回归）
5. POST suppress 命中 agent_id → **dispatch 未被调用** + `append_user_message` 被调用
6. POST suppress 传了不匹配的 id → **照样 dispatch**（减法语义）
7. POST 无 agent + 传 suppress → legacy insert，不受影响
8. preview 端点与 POST 对同一 issue 行给出一致裁决（**钉死判据共用**）
9. suppress 路径的 attachments 过滤形状 == `run_session_turn` 的形状
10. preview 对不可见 issue → 404

**前端**（vitest）
1. `will_wake=true` + 草稿非空 → chip 渲染，文案含 agent 名
2. `will_wake=false` → chip 不渲染
3. 草稿为空 → chip 不渲染
4. 点击 chip → `aria-pressed=true` + 文案变 `Won't start this time`
5. 抑制后提交 → payload 含 `suppress_agent_ids: [agentId]`
6. 未抑制提交 → payload **不含** `suppress_agent_ids` 键
7. 提交后 suppress 状态重置
8. flag off → chip 不渲染

**mock 纪律**：mock 必须 `spec=` 真类（记忆 `bug_chat_wiring_prefix_guess_sse_blind` 的教训）。

## 7. 顺手修的文档谎言

`issue_messages_router.py:5-6` 的 docstring 与 `issue_message.py:64-67` 的 schema docstring **都声称 payload 的 `agent_id` 是触发开关**：

> *"optional agent_id triggers a dispatch"*
> *"if agent_id is set the backend also dispatches an agent run"*

**handler 从未读过 `payload.agent_id`**。真正的触发源是 `issue_row.assignee_agent_id`。两处 docstring 改为如实描述，字段标 deprecated 注释（不删 —— 属 API 契约变更，另案）。

## 8. 风险

| 风险 | 缓解 |
|---|---|
| suppress 后用户以为 agent 会看到笔记但它永远不跑 | 笔记确实进历史（§3.4 已证）；agent 下次被唤醒时读得到。文案说 `Won't start **this time**` 而非 "won't see" |
| preview 与 send 之间指派人变更 | ID 列表天然 no-op（§3.3） |
| chip 判据与后端漂移 | 判据只有一个函数，两个调用方共用（§3.2）+ 测试 8 钉死 |
| 幻影行 | 绝不写 `issue_messages`（§3.5） |
| DBOS 关闭时 preview 说 will_wake=true 但 POST 500 | **已知缺口，不修** —— 评论路径今天就不守 `dbos_disabled`，preview 如实反映现状。修守卫属行为变更，超出范围 |

## 9. 交付顺序

1. 后端：`comment_trigger.py` + 判据单测 → POST 改用判据（行为不变，测试 4 守住）
2. 后端：preview 端点 + schema + 测试
3. 后端：`suppress_agent_ids` + note 路径 + 测试
4. 前端：service + chip 组件 + 接线 + 测试
5. docstring 修正
6. `/ship`（自动 merge base + bump 版本，勿手动 bump —— 记忆 #591 的坑）
