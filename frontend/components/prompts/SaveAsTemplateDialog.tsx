// frontend/components/prompts/SaveAsTemplateDialog.tsx
//
// STUB — Task 11 (B4) replaces this file with the real dialog. It exists now
// only so the props contract the shelf already passes is type-checked: a shelf
// that renders nothing is honest about an unbuilt dialog, whereas leaving the
// import out would let the call site drift from the interface B4 must honour.
import type { PromptEntry, PromptLang } from '../../services/promptsService';

export interface SaveAsTemplateDialogProps {
  scopeId: string;
  entry: PromptEntry;
  /** Album flow: the slides whose text the dialog should offer. */
  slideNames?: string[];
  lang: PromptLang;
  onClose: () => void;
  onSaved: (assetId: string) => void;
}

export function SaveAsTemplateDialog(_props: SaveAsTemplateDialogProps): null {
  return null;
}
