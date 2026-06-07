# Tag System Enrichment Design

## Goal

Enrich and optimize MediaHub's tag system for short video collection, expanding from 29 tags (6 groups) to ~39 tags (7 groups) with new "Mood" and "Technique" dimensions.

## Context

MediaHub is a short video collection platform. Users save videos from various platforms (Douyin, TikTok, YouTube, Bilibili, etc.) for mixed purposes: inspiration, entertainment, learning, and creative reference.

The current tag system has 29 tags across 6 groups, but lacks:
- Emotional/tonal dimension (healing, inspiring, etc.)
- Technical/form dimension (transitions, beat-sync, etc.)
- Some content gaps (no outdoor sports, no science/popular-science)

## Design Decisions

### Approach: Single-Dimension Optimization

Use the existing group mechanism to organize all tags — content topics, moods, and techniques all live as first-class tags in their respective groups. A video can have multiple tags across groups (e.g., "Music" + "Healing" + "Beat-sync").

Rejected alternatives:
- **Dual-dimension system** (topic + attribute via tag type) — adds UI complexity not justified for ~40 tags
- **Hierarchical tags** (parent_id) — over-engineering for this scale

### Group Restructure

Merge "Style" group (Beauty, Fashion) into "Lifestyle" — Style had only 2 tags, too small to justify a separate group.

## Final Tag Schema

### 7 Groups, 39 Tags

#### 1. Entertainment (娱乐) — 6 tags

| Tag | name_zh | Status |
|-----|---------|--------|
| Music | 音乐 | keep |
| Dance | 舞蹈 | keep |
| Comedy | 搞笑 | keep |
| Drama | 剧情 | keep |
| Gaming | 游戏 | keep |
| Variety | 综艺 | keep |

Removed: Animation (动漫) — low frequency for short video collection; anime content is typically long-form.

#### 2. Lifestyle (生活) — 8 tags

| Tag | name_zh | Status |
|-----|---------|--------|
| Food | 美食 | keep |
| Travel | 旅行 | keep |
| Pets | 宠物 | keep |
| Vlog | 日常 | keep |
| Family | 亲子 | keep |
| Cars | 汽车 | keep |
| Beauty | 颜值 | moved from Style |
| Fashion | 时尚 | moved from Style |

#### 3. Knowledge (知识) — 6 tags

| Tag | name_zh | Status |
|-----|---------|--------|
| Tech | 科技 | keep |
| AI | 人工智能 | keep |
| Finance | 财经 | keep |
| Tutorial | 教程 | keep |
| Review | 测评 | keep |
| Science | 科普 | **new** |

#### 4. Creation (创作) — 6 tags

| Tag | name_zh | Status |
|-----|---------|--------|
| Filming | 拍摄 | keep |
| Photography | 摄影 | keep |
| Post-production | 后期 | keep |
| Script | 文案 | keep |
| Story | 故事 | keep |
| Recreation | 仿拍 | keep |

#### 5. Sports (运动) — 3 tags

| Tag | name_zh | Status |
|-----|---------|--------|
| Sports | 运动 | keep |
| Fitness | 健身 | keep |
| Outdoor | 户外 | **new** |

#### 6. Mood (情绪) — 5 tags (new group)

| Tag | name_zh | Description |
|-----|---------|-------------|
| Healing | 治愈 | Warm, comforting content |
| Inspiring | 励志 | Motivational, uplifting |
| Chill | 氛围 | Relaxing, lo-fi vibes |
| Emotional | 感动 | Touching, heartfelt |
| Aesthetic | 美感 | Visually stunning composition |

#### 7. Technique (技法) — 5 tags (new group)

| Tag | name_zh | Description |
|-----|---------|-------------|
| Transition | 转场 | Creative transition techniques |
| One-take | 一镜到底 | Uncut long takes |
| Beat-sync | 卡点 | Music-rhythm matched editing |
| Slow-motion | 慢动作 | Slow-mo / ramping effects |
| Timelapse | 延时 | Timelapse photography |

## Changes Summary

| Action | Details |
|--------|---------|
| Delete group | Style |
| Create groups | Mood, Technique |
| Move tags | Beauty, Fashion → Lifestyle |
| Delete tags | Animation, Other |
| Create tags | Science, Outdoor, Healing, Inspiring, Chill, Emotional, Aesthetic, Transition, One-take, Beat-sync, Slow-motion, Timelapse |

**Net change**: 29 → 39 tags, 6 → 7 groups

## Implementation

This is a **data-only change** — no code modifications needed. The admin tags management page already supports all required operations:
- Create/delete groups
- Create/delete/move tags between groups
- Reorder groups and tags

All changes can be executed via Supabase REST API or through the admin UI.

## Group Sort Order

| Order | Group |
|-------|-------|
| 0 | Entertainment |
| 1 | Lifestyle |
| 2 | Knowledge |
| 3 | Creation |
| 4 | Sports |
| 5 | Mood |
| 6 | Technique |
