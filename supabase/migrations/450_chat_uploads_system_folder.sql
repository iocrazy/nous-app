-- 450_chat_uploads_system_folder.sql
--
-- 把历史遗留的 `name='temp'` 文件夹收养为系统文件夹：`system_key='chat_uploads'`、
-- `is_system=true`、显示名 "Chat Uploads"。**只 UPDATE，绝不 INSERT。**
--
-- ★★ 合并条件：P6 后端必须已经在线 ★★
-- ===================================
-- 这个迁移**单独一个 PR**，而且必须在 P6 清尾 PR 合并**并且后端真的起来之后**才合。
-- 这不是谨慎，是因为反过来会坏：
--
--   老后端（合并前那版 `_ensure_temp_folder`）是**按名字** get-or-create ——
--   `if folder.get("name") == "temp"`，没找到就 `create_folder({"name": "temp"})`。
--   本迁移一把那一行改名成 "Chat Uploads"，老后端的比对就**必然失配**，于是在它
--   旁边**再建一个** `name='temp'`。窗口期内每个发生过附件上传的 scope 都会长出
--   一个孤儿文件夹，新后端上线后按 key 命中被收养的那个，孤儿从此再没有代码会碰
--   它。而 P6 同批删掉了「按名字隐藏 temp」的前端过滤，所以它对用户是可见的。
--
-- 而且「迁移先到」是**常态不是竞态**：`run-migration.yml` 是几秒的 psql，
-- `deploy-gpu.yml` 是一次 docker build + 重启。放同一个 PR 里，窗口大约就是整个
-- 后端构建时长。所以顺序靠**分开合并**来保证，不靠这份 SQL 里的任何东西。
--
-- 合并前必须做两件事，第二件才是「新代码在跑」的证据（readyz 只证明进程活着）：
--   1) docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz
--   2) 真发一次带附件的聊天上传，然后确认出现了新的 keyed 行：
--      SELECT scope_id, id, name, system_key, is_system FROM folders
--       WHERE system_key = 'chat_uploads' AND is_trashed = false
--       ORDER BY id DESC LIMIT 5;
--      看不到新行 = 跑的还是老代码 = 不要合。
--
-- ★ 那这个迁移到底还管什么？——管「沉默 scope」
-- ==========================================
-- 新后端是**惰性**收养：一个 scope 只有在有人往它上传附件时才会被碰到，那时
-- `_ensure_chat_uploads_folder` 就地把它收养了（改名 + 置 key）。所以本迁移合并时
-- 剩下要做的，是那些**有历史 `temp` 但没人再上传**的 scope。
--
--   已被代码收养的     → 零变更（`system_key IS NULL` 不再匹配）
--   有 temp 但没人上传 → 收养它（本迁移存在的理由）
--   从没有过 temp      → 无候选，零变更
--
-- 代码负责活跃 scope，迁移负责沉默 scope，两者用**同一条谓词**，交集为空。
--
-- ★ 名字从来不是身份
-- =================
-- 用户可以改名，我们也可能本地化。`system_key` 才是稳定身份，`is_system` 是配套的
-- "受保护"开关（改名/移动/回收站/删除 → 409 `system_folder`，由 API 层执行；文件夹
-- 内容进出不受限）。这个道理 mig 441（封面模板）的文件头已经写死过一遍。
--
-- ★ 只收养顶层的那个，嵌套的同名文件夹一律不碰
-- ============================================
-- 候选必须 `parent_id IS NULL AND library_id IS NULL`。这不是保守起见，而是照抄
-- 历史事实：`chat_upload._ensure_temp_folder` 是**唯一**建过 `name='temp'` 文件夹的
-- 代码，它的 create 只传 `name / scope_id / created_by`，另外两列一律走 DB 默认
-- （NULL）。所以真正的附件文件夹必然在顶层、不属于任何 library。
-- 反过来，用户自己在某个项目/文件夹里建的 `temp`（草稿抽屉）如果 id 恰好更小，
-- 没有这条限定就会被收养并**锁成系统文件夹** —— 用户从此改不了名、移不动、删不掉，
-- 而他的聊天附件仍在别处。宁可那个 scope 走「新建一个空的 Chat Uploads」这条明显、
-- 可自愈的路径，也不要静默劫持用户的目录。
--
-- ★ 同 scope 多个 temp 只收养一个，其余原样留给用户
-- ================================================
-- `ux_folders_scope_system_key` 是 `(scope_id, system_key) WHERE system_key IS NOT
-- NULL AND is_trashed = false` 的部分唯一索引 —— 一个 scope 只能有一个活着的
-- `chat_uploads`。真出现两个 `temp`（历史竞态建出来过）时收养 `MIN(id)` 那个：
-- id 是 snowflake，最小即最早，也就是附件真正一直在写的那个。**其余的不动**，
-- 它们继续是普通用户文件夹 —— 静默把它们也标成系统文件夹，等于一次锁掉几个用户
-- 自己建的目录，比留着更糟。
--
-- 代码侧（`app/services/library/chat_upload.py::_ensure_chat_uploads_folder`）用的是
-- **同一条规则**。五个条件（scope / 未回收站 / `system_key IS NULL` / `parent_id IS
-- NULL` / `library_id IS NULL`）加 `MIN(id)` 必须一字不差，否则两边各收养一个不同的
-- 行，第二个撞上面那个唯一索引 —— 而且是在用户上传的那一刻炸。
-- `tests/db/test_chat_uploads_folder_migration.py::TestCodeAgreesWithTheMigration`
-- 拿真 Postgres 钉住这一条。
--
-- ★ 幂等 —— 但**不能**用来自愈顺序搞反的后果
-- ==========================================
-- 第二次跑零变更，两条出口都走到：被收养的行 `system_key` 已置位（不再匹配
-- `system_key IS NULL`），同 scope 剩下的 `temp` 被 NOT EXISTS 挡住。
-- 纯 UPDATE，不建表不改结构，schema-drift 两向都不受影响。
--
-- ⚠️ 正因为幂等，万一顺序真搞反、线上长出了第二个 `temp`，**重跑本迁移不会收编它**
-- —— 那个 scope 已经有 `chat_uploads` 了，NOT EXISTS 会挡住。运维不要以为「再跑一遍
-- 迁移就好了」。真要收编得人工判断哪个是要留的，move 内容后再删空文件夹。
--
-- `updated_at` 不在这里赋值：`update_folders_updated_at`（BEFORE UPDATE）已经拥有
-- 这一列，显式写进去也会被它覆盖。

UPDATE public.folders AS f
SET system_key = 'chat_uploads',
    is_system  = true,
    name       = 'Chat Uploads'
WHERE f.id IN (
    SELECT DISTINCT ON (c.scope_id) c.id
    FROM public.folders AS c
    WHERE c.name = 'temp'
      AND c.is_trashed = false
      AND c.system_key IS NULL
      AND c.parent_id IS NULL
      AND c.library_id IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM public.folders AS k
          WHERE k.scope_id = c.scope_id
            AND k.system_key = 'chat_uploads'
            AND k.is_trashed = false
      )
    ORDER BY c.scope_id, c.id
);
