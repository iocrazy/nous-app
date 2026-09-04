-- 450_chat_uploads_system_folder.sql
--
-- 聊天附件的落地文件夹改为系统文件夹：`system_key='chat_uploads'`，显示名
-- "Chat Uploads"。**收养**已存在的 `name='temp'` 文件夹，绝不新建行。
--
-- ★ 为什么不能只改常量
-- ===================
-- `chat_upload.py` 此前用 `TEMP_FOLDER_NAME = "temp"` 按**名字**做 get-or-create。
-- 只把那个常量改成新名字的后果是：生产上每个 scope 已存在的 `temp` 文件夹立刻
-- 失配，下一次聊天上传就在它旁边**再建一个**，历史附件全部变成孤儿——用户在
-- 素材库里看到两个文件夹，而 agent 只认新的那个。所以身份必须先迁移，代码才能
-- 换查找方式。名字从来不是身份（mig 441 的文件头把这个道理写死了）：用户可以
-- 改名，我们也可能本地化，`system_key` 才是稳定身份，`is_system` 是配套的
-- "受保护"开关（改名/移动/回收站/删除 → 409 `system_folder`，由 API 层执行，
-- 内容进出不受限）。
--
-- ★ 同 scope 多个 temp 只收养一个，其余原样留给用户
-- ================================================
-- `ux_folders_scope_system_key` 是 `(scope_id, system_key) WHERE system_key IS
-- NOT NULL AND is_trashed = false` 的部分唯一索引——一个 scope 只能有一个活着的
-- `chat_uploads`。真出现两个 `temp`（历史竞态建出来过）时，收养 `MIN(id)` 那个：
-- id 是 snowflake，最小即最早，也就是聊天附件真正一直在写的那个。**其余的不动**，
-- 它们继续是普通用户文件夹，用户可以自己改名/合并/删除——静默把它们也标成系统
-- 文件夹会一次锁掉几个用户自己建的目录，比留着更糟。
--
-- 代码侧（`app/services/library/chat_upload.py`）用**同一条规则**做兜底收养：
-- 迁移与部署没有顺序保证，先跑到的那一边收养，另一边就是零变更。两处规则必须
-- 一字不差，否则会各收养一个、撞上面那个唯一索引。
--
-- ★ 幂等
-- ======
-- 第二次跑零变更：被收养的行 `system_key` 已置位（不再匹配 `system_key IS NULL`），
-- 同 scope 剩下的 `temp` 被 NOT EXISTS 挡住（该 scope 已经有 `chat_uploads` 了）。
-- 纯 UPDATE，不建表不改结构，schema-drift 两向都不受影响。
--
-- `updated_at` 不在这里赋值：`update_folders_updated_at`（BEFORE UPDATE）已经
-- 拥有这一列，显式写进去也会被它覆盖。

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
      AND NOT EXISTS (
          SELECT 1
          FROM public.folders AS k
          WHERE k.scope_id = c.scope_id
            AND k.system_key = 'chat_uploads'
            AND k.is_trashed = false
      )
    ORDER BY c.scope_id, c.id
);
