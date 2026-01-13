---
name: progress_recorder
description: |
  Maintains project memory by automatically tracking decisions, tasks, and progress.
  Uses semantic understanding to extract and merge information into progress.md.

  AUTO-TRIGGER: This skill should be automatically applied when the conversation contains:
  - Decisions (决定/确定/选择/敲定/最终选择)
  - Constraints (必须/不能/要求/强制/禁止)
  - Tasks (需要/应该/计划/待办/TODO)
  - Completions (完成了/实现了/修复了/搞定了)
  - Important notes worth persisting

  Keywords: progress, 进度, record, archive, 归档, persist, 持久化, update progress, 更新进度, 决定, 必须, 完成, 任务
user-invocable: false
hooks:
  Stop:
    - hooks:
        - type: prompt
          prompt: |
            Review if this conversation contains any of these that should be persisted to progress.md:
            1. Decisions made (决定/确定/选择)
            2. Constraints/requirements (必须/不能/要求)
            3. New tasks or completed tasks
            4. Important technical notes

            If YES and not yet recorded, use the progress_recorder skill to update progress.md.
            If NO or already recorded, continue normally.
---

[技能说明]
    Progress Recorder 是一个智能项目记忆管理技能，专门解决长对话上下文受限导致的关键信息丢失问题。通过语义理解、高置信判定、受保护区块和智能去重，确保决策、任务、约束等关键信息被稳定持久化到 progress.md 文件中。

[核心能力]
    - **语义抽取**：依据语义而非关键词，识别 Facts/Constraints（Pinned候选）、Decisions、TODO、Done、Risks/Assumptions、Notes
    - **高置信判定**：仅在明确表达强承诺时才写入 Pinned/Decisions；包含"可能/也许"等弱化词自动降级到 Notes
    - **受保护区块**：Pinned 和 Decisions 只增不改，冲突时在 Notes 中记录而非覆盖历史
    - **TODO 管理**：为 TODO 分配唯一 ID（#1, #2...）、优先级（P0/P1/P2）、状态（OPEN/DOING/DONE）
    - **智能去重**：语义相似任务更新原条目，避免重复；新任务分配递增 ID
    - **证据追踪**：为 Done 条目附加证据指针（commit/issue/PR/路径/链接）
    - **自动归档**：Notes + Done 超过 100 条时触发归档，保持主文件精简
    - **变更合并**：增量合并新信息，保证格式一致、顺序稳定、最小扰动

[执行流程]
    第一步：文件检查与初始化
        - 检查 progress.md 是否存在
        - 若不存在：从 `templates/progress_template.md` 创建新文件，包含所有必需区块：
          * Pinned（受保护，高置信约束）
          * Decisions（受保护，按时间追加）
          * TODO（权威待办清单）
          * In Progress（进行中任务）
          * Done（最近完成的任务）
          * Risks & Assumptions（风险和假设）
          * Notes（简要要点和待确认事项）
          * Context Index（归档文件索引）
        - 若存在：读取并解析现有内容，扫描现有 TODO 确定最大 ID 值
        - 记录操作日期时间（YYYY-MM-DD HH:MM）

    第二步：语义抽取与分类（充分发挥你的语义理解能力）
        回顾当前对话的上下文，深度理解并按语义分类提取信息：

        **Pinned 候选**（高置信约束性语言）：
        - 触发词：必须/不能/要求/强制/禁止/务必/严格要求
        - 示例："必须支持 IE11"、"不能使用 GPL 协议"、"要求 99.9% 可用性"
        - 特征：长期约束、关键接口要求、依赖版本、目标环境

        **Decisions**（高置信决策语言）：
        - 触发词：决定使用/最终选择/将采用/确定方案/敲定
        - 示例："决定使用 React 开发前端"、"最终选择 PostgreSQL"
        - 特征：技术选型、架构设计、方案选择，需包含理由

        **TODO**（可执行行动项）：
        - 触发词：需要/应该/计划/待/要 + 具体任务
        - 示例："需要实现登录功能"、"应该添加单元测试"
        - 特征：明确的动词+对象，可分配 Owner 和 Context

        **Done**（完成语义）：
        - 触发词：完成了/实现了/修复了/上线了/已解决/已部署/已发布/搞定了
        - 示例："登录功能完成了"、"bug 已修复"
        - 特征：过去完成时态，尽量附加证据（commit/PR/路径）

        **Risks**（风险表述）：
        - 触发词：风险/可能导致/担心/潜在问题
        - 示例："担心性能问题"、"可能导致内存泄漏"
        - 特征：附加缓解措施（Mitigation）

        **Assumptions**（前提条件）：
        - 触发词：假设/前提/基于/依赖于/期望
        - 示例："假设用户量不超过 1 万"、"基于单机部署"
        - 特征：标注置信度（High/Med/Low）

        **Notes**（其他信息或无法高置信分类的内容）：
        - 包含所有不符合上述分类的信息
        - 特殊标注："Needs-Confirmation"（待确认事项）

        **应用高置信判定**（关键机制）：
        - 当包含弱化词时，自动降级至 Notes 并标注 "Needs-Confirmation"：
          * 弱化词：可能/也许/大概/似乎/建议/考虑/或许/可以/想
        - 边界情况优先保守处理：宁可降级不要误升级
        - 示例：
          * "必须使用 React" → Pinned（高置信）
          * "建议使用 React" → Notes: Needs-Confirmation（低置信）
          * "决定用 PostgreSQL" → Decisions（高置信）
          * "可能用 PostgreSQL" → Notes: Needs-Confirmation（低置信）

    第三步：区块级合并处理（语义去重和增量更新）
        **Pinned 区块**：
        - 仅追加高置信约束项，不修改历史
        - 检测冲突时在 Notes 记录而非修改：
          示例：Pinned 中有"必须支持 IE11"，新提到"不支持 IE"
          → 在 Notes 中记录："⚠️ CONFLICT: 新约束与 Pinned 冲突，请人工确认"

        **Decisions 区块**：
        - 按时间顺序追加，历史永不修改
        - 新决策推翻旧项时，保留历史并在 Notes 标注影响：
          示例：
          - 2026-01-10: 决定使用 MySQL
          - 2026-01-15: 改用 PostgreSQL（理由：需要 JSON 支持）
          Notes 中记录："决策变更：MySQL → PostgreSQL"

        **TODO 区块**：
        - 执行语义去重（使用你的语义理解能力）：
          * 相似任务（如"添加登录"vs"实现登录功能"）→ 更新原条目
          * 新任务 → 分配递增 ID：max(existing_ID) + 1
          * 未指定优先级时默认 P1
        - 支持状态推进：
          * OPEN → DOING：识别"开始做X"、"正在X"
          * DOING → DONE：识别完成语义，移至 Done 区块
        - 保持 ID 唯一性和单调性：ID 永不复用，删除的任务 ID 不分配给新任务

        **In Progress 区块**：
        - 识别状态为 DOING 的任务
        - 从 TODO 中提取并移入此区块
        - 格式：[P0][DOING][#ID] <任务>（Owner：<可选>，Context：<路径/链接>）

        **Done 区块**：
        - 识别完成项并移入，最近完成的放前面
        - 尽量附加证据指针：
          * commit hash：如 (evidence: commit abc123)
          * PR 链接：如 (evidence: PR #42)
          * 文件路径：如 (evidence: src/auth/login.ts)
        - 若无证据不要虚构，可留空

        **Risks & Assumptions 区块**：
        - 直接追加新识别的风险或假设
        - Risk 附加缓解措施：Risk：<描述>（Mitigation：<措施>）
        - Assumption 标注置信度：Assumption：<描述>（Confidence：High/Med/Low）

        **Notes 区块**：
        - 记录简要要点、待确认事项、冲突提示
        - 标注 "Needs-Confirmation" 的低置信信息
        - 记录 Pinned/Decisions 的冲突提示

    第四步：一致性验证与输出
        - 检查 TODO ID 唯一性和单调性
        - 验证受保护区块（Pinned/Decisions）未被意外修改
        - 确认所有新增条目都有日期时间戳（YYYY-MM-DD）
        - 更新文件头：_Last updated: YYYY-MM-DD HH:MM_
        - 写入更新后的 progress.md

    第五步：智能归档（语义总结，可选）
        **触发条件**（满足任一）：
        - Notes + Done 合计 > 100 条
        - 用户显式要求归档（如"归档这个月的进度"）
        - 项目进入新阶段/里程碑（如"完成认证模块，开始支付模块"）

        **归档流程**（充分发挥语义理解和总结能力）：

        第一步：识别归档范围
        - 分析 Notes 和 Done 中的任务，识别自然的阶段边界：
          * 按时间分组（如按月、按冲刺周期）
          * 按功能模块分组（如"用户认证模块"、"支付模块"）
          * 按里程碑分组（如"v1.0 发布"、"Beta 测试阶段"）
        - 确定本次归档的主题和范围

        第二步：语义总结和提炼
        - **不是简单复制粘贴**，而是深度理解和总结：

          原始记录示例（零散的 50 条 Done）：
          ```
          - 2026-01-05: [#1] 实现 JWT token 生成
          - 2026-01-06: [#2] 添加 token 过期检查
          - 2026-01-07: [#3] 实现 refresh token 机制
          - 2026-01-08: [#4] 添加用户登录接口
          - 2026-01-09: [#5] 添加用户注册接口
          ... （共 50 条零散任务）
          ```

          归档总结示例（提炼后）：
          ```markdown
          ## [Phase 1.1] 用户认证模块 - 2026-01

          ### 📋 概述
          完成用户认证系统 v1.0，包括 JWT 认证、用户登录注册、权限管理三大功能

          ### ✅ 主要成果
          - JWT 认证机制：实现 token 生成、过期检查、refresh token 机制
          - 用户管理：登录、注册、密码重置、邮箱验证
          - 权限系统：基于 RBAC 的权限模型，支持角色和权限分配

          ### 🎯 关键决策
          - 2026-01-02: 决定使用 JWT 而非 Session（理由：无状态，易于扩展到微服务）
          - 2026-01-05: 选择 PostgreSQL 存储用户数据（理由：需要 JSON 字段支持）

          ### 🔧 技术亮点
          - 实现了双 token 机制（access token + refresh token）提升安全性
          - 使用 bcrypt 加盐哈希存储密码，防止彩虹表攻击
          - 集成 nodemailer 实现邮箱验证和密码重置

          ### 📚 经验教训
          - **成功经验**：提前设计好权限模型，避免后期重构
          - **改进空间**：IE11 兼容性问题花费较多时间，下次提前准备 polyfill
          - **避免踩坑**：JWT secret 必须足够长（至少 256 位），不要使用简单字符串

          ### 📊 数据统计
          - 完成任务：18 个
          - 新增代码：约 3000 行
          - 主要文件：src/auth/*, src/models/User.js
          - 时间跨度：2026-01-02 ~ 2026-01-20

          ### 🔗 相关链接
          - 主要 PR：#42, #45, #48
          - 设计文档：docs/auth-design.md
          ```

        第三步：生成归档内容
        - 使用 `templates/archive_template.md` 作为结构参考
        - 根据实际情况调整结构（不要死板套用模板）
        - 确保总结内容：
          * **简洁精炼**：不要罗列所有细节，抓住关键点
          * **有价值**：未来回顾时能快速了解这个阶段做了什么
          * **可追溯**：保留关键链接（PR、commit、文档）
          * **有洞察**：总结经验教训，不只是罗列事实

        第四步：更新文件
        - 若 progress.archive.md 不存在：
          * 从 `templates/archive_template.md` 创建
          * 写入第一个归档段落
        - 若已存在：
          * 读取现有内容
          * 在文件开头插入新归档段落（最新的在最上面）
          * 旧内容保持不变，只增不删

        - 清理 progress.md：
          * 保留最近 30 天或 50 条 Notes/Done（较大者）
          * 删除已归档的旧条目
          * 受保护区块（Pinned/Decisions/TODO）永不删除

        - 更新 progress.md 的 Context Index：
          ```markdown
          ## Context Index
          - Archive：./progress.archive.md
          - Latest Archive：[Phase 1.1] 用户认证模块 - 2026-01
          ```

        第五步：返回归档摘要
        ```
        ✅ Archive Completed - YYYY-MM-DD

        📦 Archived Phase:
        [Phase 1.1] 用户认证模块 - 2026-01

        📝 Summary:
        - Archived: 50 tasks → summarized into 1 milestone
        - Key achievements: JWT 认证、用户管理、权限系统
        - Lessons learned: 3 insights documented

        📊 Cleanup:
        - progress.md reduced: 520 lines → 180 lines
        - Kept recent: 30 notes + 20 done items
        ```

    第六步：生成更新摘要并返回
        显示更新摘要：
        ```
        ✅ Progress Updated - YYYY-MM-DD HH:MM

        📝 Changes:
        - Added: <N> new tasks
        - Updated: <N> existing tasks
        - Completed: <N> tasks
        - Archived: <N> tasks (if applicable)
        - New Decisions: <N>
        - New Constraints: <N>

        ⚠️ Attention:
        - Needs-Confirmation: <N> items
        - Conflicts: <冲突列表>

        📊 Current Status:
        - Total TODO: <N>
        - In Progress: <N>
        - Done (recent): <N>
        - Next P0 actions: <top 3 priorities>
        ```

        返回："✅ progress.md 已更新完成"

[注意事项]
    - **充分发挥语义理解能力**：
      * 不要依赖简单的关键词匹配，要深度理解对话含义
      * 识别隐含任务（"这块需要优化" → TODO: 优化XX模块）
      * 理解口语化、模糊的表达
      * 根据上下文推断任务的实际状态和优先级

    - **高置信判定是核心**：
      * 只有包含"必须/决定/确定"等强承诺词才写入受保护区块
      * 包含"可能/也许/建议"等弱化词自动降级到 Notes
      * 边界情况保守处理：宁可降级不要误升级
      * 不确定时标注 "Needs-Confirmation"

    - **受保护区块不可破坏**：
      * Pinned 和 Decisions 一旦写入就成为"宪法"，永不自动修改
      * 检测到冲突时在 Notes 中提醒，不擅自修改历史
      * 新决策推翻旧决策时，保留两者并在 Notes 说明影响

    - **TODO ID 管理严格**：
      * 每个 TODO 有唯一 ID（#1, #2, #3...）
      * ID 单调递增且永不复用
      * 即使任务删除，该 ID 也不再分配给新任务
      * 扫描现有 TODO 确定 max(ID)，新任务 = max + 1

    - **语义去重要准确**：
      * "添加登录功能" = "实现用户登录" = "做登录模块" → 同一任务
      * "优化登录速度" ≠ "添加登录功能" → 不同任务
      * 相似任务更新原条目，补充细节或更新状态
      * 不确定时倾向于保留（宁可多记录，不要漏掉）

    - **证据追踪要主动**：
      * Done 条目尽量附加 commit hash、PR 链接、文件路径
      * 若对话中提到相关证据，一定要记录
      * 若无证据不要虚构

    - **归档是语义总结**：
      * 不是简单的文本搬运，而是深度理解和提炼
      * 将零散的任务总结为结构化的里程碑
      * 提炼关键成果、技术决策、经验教训
      * 保留可追溯性（PR、commit、文档链接）
      * 最新的归档在 archive.md 最上面，便于查阅
      * 归档前建议创建备份

    - **时间戳必需**：
      * 每次更新文件头：_Last updated: YYYY-MM-DD HH:MM_
      * Decisions 添加日期：YYYY-MM-DD: <决策>
      * Done 添加日期：YYYY-MM-DD: [#ID] <任务>
      * Notes 添加日期：YYYY-MM-DD: <记录>

    - **保持格式一致**：
      * 遵循模板的区块顺序和格式
      * 保持 Markdown 的可读性
      * 使用统一的列表符号和缩进

    - **完全依赖语义能力**：
      * 所有解析、合并、归档任务都由 Claude 的语义理解完成
      * 不依赖任何 Python 脚本或外部工具
      * 充分发挥 LLM 的语义理解、总结提炼、上下文关联能力

    - **文件路径灵活**：
      * 默认使用项目根目录的 progress.md
      * 若用户指定路径（如 docs/progress.md），使用指定路径

    - **备份保护**：
      * 重大更新前先备份：`cp progress.md .backup/progress_$(date +%Y%m%d_%H%M%S).md`
      * 归档前自动备份

    - **协作模式**：
      * 支持与其他 Skills（如 updatelog、commit）配合使用
      * 可以从 progress.md 中读取任务状态用于其他用途
