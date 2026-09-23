-- 493 — 平台转录定价：0.8 元/小时 = 80 积分/小时（用户 2026-09-23 裁定）
--
-- 换算口径：1 积分 = 1 分钱。依据是系统里唯一在扣分的路径——agent run 的
-- 「一个回合的积分 = ceil(整棵树的平台花费之和，单位 分)」（tree_charge.py 开头）。
-- point_packages 目前为空（没有「几元买多少积分」的对外价），所以沿用这条口径。
--
-- 计费机制见 PR #2395：转写成功收尾时按实际时长扣一次，
-- 积分 = ceil(秒 × pricing_value / 3600)；BYOK 不扣；价格 0 = 免费；只向前不追扣。
-- 1 分钟 → 2，10 分钟 → 14，30 分钟 → 40，1 小时 → 80。
--
-- 只动平台自托管的 ASR 行（actual_provider='nous'，type='asr'），并且只在它仍是
-- per_hour、价格仍为 0 时改——admin 里若已手动定过价，本迁移不覆盖。
-- 幂等：第二次运行时价格已是 80，条件不再命中。空库上零行命中，安全。

UPDATE public.nous_models
   SET pricing_value = 80,
       updated_at    = now()
 WHERE type = 'asr'
   AND actual_provider = 'nous'
   AND pricing_type = 'per_hour'
   AND pricing_value = 0;
