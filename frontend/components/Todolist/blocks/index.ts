/**
 * The built-in issue blocks, registered explicitly (no import side effects
 * elsewhere): importing this module once installs them. A new block = one
 * file in this folder + one line here.
 */
import { registerIssueBlock, registeredIssueBlocks } from '../issueBlocks';
import { budgetBlock } from './BudgetBlock';
import { cockpitBlock } from './CockpitBlock';
import { deliverablesBlock } from './DeliverablesBlock';
import { linksBlock } from './LinksBlock';
import { outputsBlock } from './OutputsBlock';
import { schedulesBlock } from './SchedulesBlock';
import { stageBriefBlock } from './StageBriefBlock';
import { statusBlock } from './StatusBlock';
import { subtasksBlock } from './SubtasksBlock';

export const BUILTIN_ISSUE_BLOCKS = [
  cockpitBlock,
  statusBlock,
  stageBriefBlock,
  deliverablesBlock,
  outputsBlock,
  subtasksBlock,
  budgetBlock,
  schedulesBlock,
  linksBlock,
];

export function ensureBuiltinIssueBlocks(): void {
  const have = new Set(registeredIssueBlocks());
  for (const block of BUILTIN_ISSUE_BLOCKS) {
    if (!have.has(block.id)) registerIssueBlock(block);
  }
}

ensureBuiltinIssueBlocks();
