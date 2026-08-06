// frontend/components/AILibrary/AgentCostTab.tsx
// B2 — "what has this agent cost" (spec 2026-08-02 §04, split out).
//
// Actual spend and the activity behind it: the 14-day charts, the token/cost
// rollup, and which product surfaces invoke this agent.
//
// It was the bottom half of the Profile tab, under the budget inputs. Two
// unrelated questions shared that scroll — "how has spend been trending"
// (here) and "what did this prompt look like last week" (version history) —
// and the budget fields at the top made the whole page read as if it were
// about money, then wasn't. Profile keeps the settings you write; this keeps
// the numbers you read.

import React from 'react';
import { AgentDashboardTab } from './AgentDashboardTab';

interface AgentCostTabProps {
  slug: string;
}

export const AgentCostTab: React.FC<AgentCostTabProps> = ({ slug }) => (
  // `onOpenRuns` is deliberately absent: the runs surface lives on the
  // Workbench, and a link that switches tabs under you reads as a bug.
  <div data-testid="agent-cost-tab">
    <AgentDashboardTab slug={slug} onOpenRuns={undefined} />
  </div>
);

export default AgentCostTab;
