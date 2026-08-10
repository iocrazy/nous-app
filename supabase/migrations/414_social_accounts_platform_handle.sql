-- 414 — social_accounts.platform_handle: 把「可改的展示名」从身份键里拆出来
--
-- P0-1（docs/superpowers/specs/2026-08-09-distribution-gap-closure-plan.md）。
--
-- 病因不在这张表，而在写它的那条路径：nous-browser 的抖音 profile 解析器有
-- **两条** platform_user_id 提取路径 —— 先试 DOM 选择器 `[class^="unique_id-"]`
-- （抖音号），落空才退回 cookie `uid_tt`。两条路径产出的是不同命名空间的值，
-- 而本表的唯一键是 (scope_type, scope_id, platform, platform_user_id)：
--
--     08-06  MioPoo  platform_user_id = 41cf16775ee3e9fdf5e021f9c1ddfc12  ← cookie
--     08-09  MioPoo  platform_user_id = miopoo                            ← DOM
--
-- 同一个账号两行，10 条发布记录留在第一行，UI 显示的是第二行。哪条路径赢取决
-- 于当次页面渲染 —— 也就是说，唯一键的语义是随机的。
--
-- 代码侧的修法是"每平台一个权威 cookie，取不到就以 identity_unresolved 类型化
-- 失败"（browser/app/login.py::identity_from_cookies）。本迁移只负责这件事的
-- 另一半：抖音号是**可改的展示信息**，它需要一个自己的列，从此参与显示、不参
-- 与身份。
--
-- ⚠️ 唯一键**不动**。platform_handle 不进 UNIQUE，也不该进：用户改一次抖音号
-- 就多一个账号，正是我们在修的病。

ALTER TABLE public.social_accounts
    ADD COLUMN IF NOT EXISTS platform_handle TEXT;

COMMENT ON COLUMN public.social_accounts.platform_handle IS
    '平台侧的公开账号名（抖音号 / 小红书号）。展示用，用户可随时改名，'
    '因此**不参与**唯一键 (scope_type, scope_id, platform, platform_user_id)。'
    '身份键由 nous-browser 每平台声明的单一 cookie 决定'
    '（抖音 uid_tt / B 站 DedeUserID），见 mig 414 的说明。';

-- ── 存量数据归一：实测为空操作 ────────────────────────────────────────────
--
-- 上线前按要求做了 dry-run（2026-08-09，生产库 nous-db）：把每一行 session 账号
-- 的 session_state 在 nous-backend 容器里解密，取出该平台的权威 cookie，与已存
-- 的 platform_user_id 逐行比对。凭证明文没有落盘、没有进日志，脚本只输出布尔值
-- 与 6 位掩码前缀。
--
--   id=335617669826935 platform=douyin cookie=uid_tt present=True
--   matches_current=True current=41cf16...(len=32) cookie_value=41cf16...(len=32)
--   cookie_count=46 has_uid_tt=True has_uid_tt_ss=True
--   --- rows_examined=1 rows_that_would_be_rewritten=0
--
-- 库里唯一的 session 账号已经键在 uid_tt 上（08-09 那行 `miopoo` 已被用户手工
-- 删掉），所以**没有需要改写的行，这里不写 UPDATE**。
--
-- 之所以留下这段而不是直接省略：`41cf16…` 是 32 位 hex，"形状像 uid_tt" 很容易
-- 被当成"那就是 uid_tt"而跳过核对 —— 而形状相同的值恰恰是这类 bug 最擅长的伪
-- 装。结论来自实测，不是来自形状。
--
-- platform_handle 存量一律留 NULL：那行账号的抖音号我们手上没有可信来源
-- （唯一见过它的那行已被删除）。它会在下一次 /accounts/{id}/refresh 或重新扫码
-- 时由 update_profile / upsert_session_account 自动补上。
--
-- 若将来这里真的需要归一，务必先跑同样的 dry-run：SQL 里无法解密 session_state，
-- 任何"看起来像"的判断都不能作为改写依据。
