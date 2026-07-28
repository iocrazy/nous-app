-- 384: 修复 resource_versions.file_path 残留的日期桶旧路径
--
-- 背景
-- ----
-- 2026-02-25 在 nas-A 上做过一次 "YYYY-MM/ 日期桶 → global/resources/web/ 新布局"
-- 的路径迁移。当时那份 migration_sql.sql 更新了 parsed_media(100 条) 和
-- resources(97 条),但**完全没有触碰 resource_versions**,于是 92 行版本记录
-- 的 file_path 一直停在迁移前的旧地址,例如:
--
--   resource_versions.file_path = '2026-01/7589594610905779496_和前任铐在一起….mp4'  ← 已不存在
--   resources.file_path         = 'global/resources/web/douyin/277988279332976/video.mp4'  ← 文件在这
--
-- 文件本身一个都没丢(NAS 与 gpupc 各一份、逐字节一致、ffprobe 时长与 DB 记录
-- 逐个精确相等),坏的只是版本行里的这个字符串。
--
-- 影响
-- ----
-- backend/app/api/resources_versions_router.py 的版本下载端点直接读
-- version["file_path"] 发文件,所以这 92 个资源的"版本历史下载"必然 404。
-- 主下载 / 详情页读的是 resources 行(已是新路径),所以一直没被发现。
--
-- 为什么只对齐这 92 行,不做全表一致性同步
-- --------------------------------------
-- 全表 939 行里有 353 行的 resources.file_path 是 **NULL 且刻意如此** ——
-- PR-B(4404a843, 2026-04-26, #131)确立 "resources 只存 per-user 独立状态,
-- file_path 等共享下载字段只存 parsed_media"。对那些行做同步会逆着设计走,
-- 把正确的路径抹掉。因此这里用 WHERE 精确限定在日期桶模式上。
--
-- 对齐目标取 parsed_media.download_path:按 PR-B 它是共享下载字段的唯一真相。
-- (已核验:这 92 行的 pm.download_path 与 r.file_path 92/92 完全相同且均非空。)
--
-- file_size_bytes 不在本次范围
-- --------------------------
-- 这 92 行里有 51 行的 file_size_bytes 与磁盘不符,但**两张表记的值完全相同**,
-- 且它们 transcode_status 全为 NULL —— 说明不是本次迁移或转码造成的,而是
-- 2026-02 那批下载的记录 bug(按月统计:2026-02 = 51 对/53 错,2026-03 = 167/6,
-- 2026-04 起 431/2,即三月已自愈)。属于独立的历史问题,另行处理。

BEGIN;

-- 先留档:把即将改动的行快照进临时表,便于本事务内校验
CREATE TEMP TABLE _mig384_before ON COMMIT DROP AS
SELECT rv.id, rv.resource_id, rv.file_path AS old_path, pm.download_path AS new_path
FROM resource_versions rv
JOIN resources r ON r.id = rv.resource_id
JOIN parsed_media pm ON pm.id = r.media_id
WHERE rv.file_path ~ '(^|/)20[0-9]{2}-[0-9]{2}/'
  AND pm.download_path IS NOT NULL
  AND pm.download_path !~ '(^|/)20[0-9]{2}-[0-9]{2}/';

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM _mig384_before;
    RAISE NOTICE '[384] 待修复行数: %', n;
    -- 守卫:预期正好 92 行。多于此说明出现了新的日期桶数据(不应该发生),
    -- 少于此说明已被部分修过 —— 两种情况都值得人看一眼,但不阻断:
    -- 迁移本身幂等,重复执行是空操作。
    IF n > 92 THEN
        RAISE WARNING '[384] 待修复行数 % 超出预期的 92,请复核是否有新的日期桶写入', n;
    END IF;
END $$;

UPDATE resource_versions rv
SET file_path = b.new_path
FROM _mig384_before b
WHERE rv.id = b.id;

-- 校验:改完之后不应再有任何日期桶残留
DO $$
DECLARE
    leftover integer;
BEGIN
    SELECT count(*) INTO leftover
    FROM resource_versions
    WHERE file_path ~ '(^|/)20[0-9]{2}-[0-9]{2}/';

    IF leftover > 0 THEN
        RAISE EXCEPTION '[384] 仍有 % 行日期桶路径未修复,回滚', leftover;
    END IF;

    RAISE NOTICE '[384] 完成,resource_versions 中已无日期桶路径残留';
END $$;

COMMIT;
