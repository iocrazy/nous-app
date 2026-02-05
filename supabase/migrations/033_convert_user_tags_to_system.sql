-- Migration: Convert user tags to system tags
-- These tags should be system-wide predefined tags

-- Update tag type from 'user' to 'system' for predefined tags
-- Also set user_id to NULL since system tags don't belong to any user
UPDATE tags
SET
    type = 'system',
    user_id = NULL
WHERE name IN (
    'Drama', '剧情',
    'Text', '文字',
    'Family', '亲子',
    'Filming', '拍摄',
    'Post-production', '后期',
    'Recreation', '仿拍',
    'Finance', '财经',
    'Variety', '综艺'
) AND type = 'user';

-- Also update by Chinese name if they were created with Chinese names
UPDATE tags
SET
    type = 'system',
    user_id = NULL,
    name_zh = CASE name
        WHEN '剧情' THEN '剧情'
        WHEN '文字' THEN '文字'
        WHEN '亲子' THEN '亲子'
        WHEN '拍摄' THEN '拍摄'
        WHEN '后期' THEN '后期'
        WHEN '仿拍' THEN '仿拍'
        WHEN '财经' THEN '财经'
        WHEN '综艺' THEN '综艺'
        ELSE name_zh
    END
WHERE name IN ('剧情', '文字', '亲子', '拍摄', '后期', '仿拍', '财经', '综艺')
  AND type = 'user';

-- Verify the changes
SELECT id, name, name_zh, type, user_id FROM tags WHERE type = 'system' ORDER BY name;
