// Allow-list of lucide icons agents can pick from, and a resolver that maps
// an agent's stored icon slug back to the actual lucide component. Keeping
// this explicit (instead of an open-ended lucide lookup) means:
//   - The picker has a curated, usable set rather than 1000+ icons
//   - Tree-shaking only pulls in the icons we actually ship
//   - Unknown / renamed slugs fall back safely to Bot
//
// Adding a new icon: add its import, then add the {slug, Icon} pair below.

import type { LucideIcon } from 'lucide-react';
import {
  Bot,
  Sparkles,
  BrainCircuit,
  Wand2,
  Zap,
  Code,
  Terminal,
  FileText,
  Pencil,
  PenTool,
  Palette,
  Camera,
  Film,
  Music,
  Mic,
  Headphones,
  Image,
  Video,
  MessageSquare,
  MessagesSquare,
  Search,
  Bookmark,
  Book,
  GraduationCap,
  Lightbulb,
  Target,
  Rocket,
  Flame,
  Heart,
  Star,
  Compass,
  Map,
  Globe,
  Building,
  Briefcase,
  Calculator,
  Wrench,
  Cog,
  Hammer,
  Scissors,
  Package,
  Boxes,
  Database,
  Server,
  Cloud,
  ShieldCheck,
  ChartBar,
  TrendingUp,
  DollarSign,
} from 'lucide-react';

export interface AgentIconOption {
  slug: string;
  Icon: LucideIcon;
}

export const AGENT_ICON_OPTIONS: readonly AgentIconOption[] = [
  { slug: 'bot', Icon: Bot },
  { slug: 'sparkles', Icon: Sparkles },
  { slug: 'brain-circuit', Icon: BrainCircuit },
  { slug: 'wand', Icon: Wand2 },
  { slug: 'zap', Icon: Zap },
  { slug: 'code', Icon: Code },
  { slug: 'terminal', Icon: Terminal },
  { slug: 'file-text', Icon: FileText },
  { slug: 'pencil', Icon: Pencil },
  { slug: 'pen-tool', Icon: PenTool },
  { slug: 'palette', Icon: Palette },
  { slug: 'camera', Icon: Camera },
  { slug: 'film', Icon: Film },
  { slug: 'music', Icon: Music },
  { slug: 'mic', Icon: Mic },
  { slug: 'headphones', Icon: Headphones },
  { slug: 'image', Icon: Image },
  { slug: 'video', Icon: Video },
  { slug: 'message', Icon: MessageSquare },
  { slug: 'chat', Icon: MessagesSquare },
  { slug: 'search', Icon: Search },
  { slug: 'bookmark', Icon: Bookmark },
  { slug: 'book', Icon: Book },
  { slug: 'graduation', Icon: GraduationCap },
  { slug: 'lightbulb', Icon: Lightbulb },
  { slug: 'target', Icon: Target },
  { slug: 'rocket', Icon: Rocket },
  { slug: 'flame', Icon: Flame },
  { slug: 'heart', Icon: Heart },
  { slug: 'star', Icon: Star },
  { slug: 'compass', Icon: Compass },
  { slug: 'map', Icon: Map },
  { slug: 'globe', Icon: Globe },
  { slug: 'building', Icon: Building },
  { slug: 'briefcase', Icon: Briefcase },
  { slug: 'calculator', Icon: Calculator },
  { slug: 'wrench', Icon: Wrench },
  { slug: 'cog', Icon: Cog },
  { slug: 'hammer', Icon: Hammer },
  { slug: 'scissors', Icon: Scissors },
  { slug: 'package', Icon: Package },
  { slug: 'boxes', Icon: Boxes },
  { slug: 'database', Icon: Database },
  { slug: 'server', Icon: Server },
  { slug: 'cloud', Icon: Cloud },
  { slug: 'shield', Icon: ShieldCheck },
  { slug: 'chart', Icon: ChartBar },
  { slug: 'trending', Icon: TrendingUp },
  { slug: 'dollar', Icon: DollarSign },
];

const ICON_BY_SLUG: Record<string, LucideIcon> = Object.fromEntries(
  AGENT_ICON_OPTIONS.map((o) => [o.slug, o.Icon]),
);

/** Default icon used when an agent has no icon set, or carries an unknown slug. */
export const DEFAULT_AGENT_ICON: LucideIcon = Bot;

/**
 * Resolve an agent's stored icon slug to a lucide component. Unknown slugs
 * fall back to the default Bot icon — never throws, never returns undefined.
 */
export function getAgentIcon(slug?: string | null): LucideIcon {
  if (!slug) return DEFAULT_AGENT_ICON;
  return ICON_BY_SLUG[slug] ?? DEFAULT_AGENT_ICON;
}
