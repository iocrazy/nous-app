-- 428_music_charts.sql
--
-- 抖音「选择音乐」面板的榜单缓存。
--
-- 为什么要缓存而不是点开就取：那两个接口（media/music/category 与
-- media/music/list）的签名绑定整个 query，实测（2026-08-19，重放阶梯）**改任何
-- 一个参数都返回 status_code=8**，所以我们拼不出这样一个 URL——请求必须由抖音
-- 自己的页面发出。而那个页面（图文编辑器）**只在上传素材之后才存在**：不传素材
-- 的那一轮 URL 停在 /upload、「选择音乐」0 匹配。
--
-- 于是读一次榜单 = 开一个浏览器 + 上传一个文件 ≈ 40-70 秒，并在账号上留下一条
-- 草稿。这个代价不能挂在"用户点开面板"这个动作上，所以：定时采集 → 落这两张表
-- → 面板读表。
--
-- ⚠️ 按账号存，不是全局。「收藏」显然是账号私有的，「推荐」实测也是个性化的；
-- 把一个账号的收藏夹展示给另一个账号是数据泄漏，不是缓存优化。
--
-- ⚠️ (category_kind, category_id) 一起才是身份。实测「推荐」与「收藏」**都是
-- category_id='1'**，只靠 type 区分——唯一键少写 kind 会让两个榜单互相覆盖，
-- 而且看起来一切正常。
--
-- RLS 不启用：后端内部表，不经 PostgREST，可见性由 API 层按账号归属闸门控制
-- （与 agent_permission_audits / script_shot_ops 同口径）。

CREATE TABLE IF NOT EXISTS public.music_charts (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  account_id BIGINT NOT NULL REFERENCES public.social_accounts(id) ON DELETE CASCADE,
  platform VARCHAR(32) NOT NULL,
  -- 平台自己的分类 id 与类型。两列一起才是身份，见文件头。
  category_id TEXT NOT NULL,
  category_kind TEXT NOT NULL,
  category_name TEXT NOT NULL,
  -- 面板里的排列顺序，采集时原样记下——tab 的先后是产品编排，不是我们能推的。
  position INTEGER NOT NULL DEFAULT 0,
  -- ⚠️ 三态里的两态在这里分开：ok=true + 零首 = 真的空（实测：没收藏过歌的账号，
  -- 收藏接口回 137 字节、连 songs 键都没有）；ok=false = 这次没读到。合并的后果
  -- 是要么把空收藏夹永远显示成坏了，要么把抓取失败显示成"平台上就是没有"。
  ok BOOLEAN NOT NULL DEFAULT FALSE,
  error TEXT NOT NULL DEFAULT '',
  -- 平台自己的翻页游标，原样存，供将来"加载更多"接着走。
  cursor TEXT NOT NULL DEFAULT '',
  has_more BOOLEAN NOT NULL DEFAULT FALSE,
  -- ⚠️ 两个时间戳，不是一个。fetched_at 说的是**现在存着的这批歌是什么时候读到
  -- 的**（只在成功时前进），checked_at 说的是**最后一次尝试**（每次都前进）。
  -- 合成一个的后果很具体：一次失败的采集会给昨天的数据打上"刚刚更新"，于是
  -- UI 上"更新于 1 分钟前"这句话在采集已经连挂三天时依然成立。
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT music_charts_identity_key
    UNIQUE (account_id, category_kind, category_id)
);

CREATE INDEX IF NOT EXISTS idx_music_charts_account
  ON public.music_charts (account_id, position);

CREATE TABLE IF NOT EXISTS public.music_chart_tracks (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  chart_id BIGINT NOT NULL REFERENCES public.music_charts(id) ON DELETE CASCADE,
  -- 榜单内位次。榜单**是**一个有序列表，丢掉顺序就丢掉了它的全部含义。
  position INTEGER NOT NULL,
  -- ⚠️ TEXT，不是 BIGINT。这是抖音曲库的 19 位 id，平台在 list 接口上就是按
  -- 字符串给的；转成数字会在 JS 端（>2^53）丢精度，而前端选歌正是拿它做键。
  music_id TEXT NOT NULL,
  music_name TEXT NOT NULL,
  music_author TEXT NOT NULL DEFAULT '',
  duration_s INTEGER NOT NULL DEFAULT 0,
  -- ⚠️ NULL ≠ 0。0 是真实的目录值（2026-08-17 那次生产拒绝就是一首 0 人使用的
  -- 歌），NULL 是"平台没说"。用 NOT NULL DEFAULT 0 会把两者抹平。
  user_count BIGINT NULL,
  cover_url TEXT NOT NULL DEFAULT '',
  -- 试听地址。平台给的是有时效的 CDN 直链，所以它跟着每次采集一起刷新，
  -- 不做长期承诺。
  play_url TEXT NOT NULL DEFAULT '',
  CONSTRAINT music_chart_tracks_position_key UNIQUE (chart_id, position)
);

CREATE INDEX IF NOT EXISTS idx_music_chart_tracks_chart
  ON public.music_chart_tracks (chart_id, position);

-- 同一首歌会出现在多个榜单里，按 id 查"它在哪些榜上"是选歌面板的常见动作。
CREATE INDEX IF NOT EXISTS idx_music_chart_tracks_music
  ON public.music_chart_tracks (music_id);
