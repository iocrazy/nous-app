// Icon resolution for skill cards (spec §05: the card name carries an icon).
//
// Why this is not just `skill.icon`: that column stores an EMOJI — the seeds
// ship `icon: 🎬` / `📝` / `🌿`, and users type one into a free-text field in
// NewSkillModal. Emoji are banned from this UI (they render differently on
// every platform and clash with the lucide set used everywhere else), so the
// column is deliberately never read here. The icon is derived from the
// skill's category — or, when that free-text field is blank, from its name
// and slug — and falls back to a neutral document icon.
//
// Adding a rule: put the more specific keyword FIRST, since the first match
// wins ("short video script" should read as a script, not as a video).

import type { LucideIcon } from 'lucide-react';
import {
  FileText,
  Film,
  Mic,
  Palette,
  PenTool,
  ScrollText,
  Search,
  Send,
  Video,
  Wrench,
} from 'lucide-react';

interface SkillIconInput {
  category?: string | null;
  name?: string | null;
  slug?: string | null;
}

const RULES: ReadonlyArray<{ match: RegExp; Icon: LucideIcon }> = [
  { match: /storyboard|shotlist|shot-list|分镜|镜头/, Icon: Film },
  { match: /script|screenplay|剧本|outline|大纲|branch|分支/, Icon: ScrollText },
  { match: /social|publish|post|发布|平台/, Icon: Send },
  { match: /copy|文案|writing|write|营销/, Icon: PenTool },
  { match: /design|art|visual|palette|视觉|美术|设计/, Icon: Palette },
  { match: /video|视频|clip|剪辑/, Icon: Video },
  { match: /audio|voice|music|口播|配音/, Icon: Mic },
  { match: /research|analy|search|检索|分析|调研/, Icon: Search },
  { match: /tool|util|工具/, Icon: Wrench },
];

/** Used when nothing matches — a skill is a document above all else. */
export const DEFAULT_SKILL_ICON: LucideIcon = FileText;

/**
 * Pick the lucide icon for a skill. Never throws and never returns undefined,
 * so callers can render the result directly.
 */
export function skillIconFor(skill: SkillIconInput): LucideIcon {
  const haystack = [skill.category, skill.name, skill.slug]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  if (!haystack) return DEFAULT_SKILL_ICON;
  for (const rule of RULES) {
    if (rule.match.test(haystack)) return rule.Icon;
  }
  return DEFAULT_SKILL_ICON;
}

export default skillIconFor;
