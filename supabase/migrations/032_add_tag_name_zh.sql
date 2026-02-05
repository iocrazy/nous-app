-- Migration: Add Chinese name support for bilingual tags
-- This adds a name_zh column for Chinese translations

-- Add name_zh column
ALTER TABLE tags ADD COLUMN IF NOT EXISTS name_zh VARCHAR(50);

-- Create index for Chinese name search
CREATE INDEX IF NOT EXISTS idx_tags_name_zh ON tags(name_zh);

-- Update existing system tags with Chinese translations
UPDATE tags SET name_zh = CASE name
  WHEN 'Food' THEN '美食'
  WHEN 'Tutorial' THEN '教程'
  WHEN 'Comedy' THEN '搞笑'
  WHEN 'Dance' THEN '舞蹈'
  WHEN 'Music' THEN '音乐'
  WHEN 'Beauty' THEN '颜值'
  WHEN 'Fashion' THEN '时尚'
  WHEN 'Gaming' THEN '游戏'
  WHEN 'Pets' THEN '宠物'
  WHEN 'Travel' THEN '旅行'
  WHEN 'Tech' THEN '科技'
  WHEN 'Sports' THEN '运动'
  WHEN 'Vlog' THEN '日常'
  WHEN 'Other' THEN '其他'
  WHEN 'Drama' THEN '剧情'
  WHEN 'Text' THEN '文字'
  WHEN 'Family' THEN '亲子'
  WHEN 'Filming' THEN '拍摄'
  WHEN 'Post-production' THEN '后期'
  WHEN 'Recreation' THEN '仿拍'
  WHEN 'Finance' THEN '财经'
  WHEN 'Variety' THEN '综艺'
  ELSE name_zh
END
WHERE type = 'system' AND name_zh IS NULL;

-- Add comment
COMMENT ON COLUMN tags.name_zh IS 'Chinese translation of tag name for bilingual support';
