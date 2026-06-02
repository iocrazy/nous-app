import type { Tag } from '../../types';

export interface EagleTagPickerProps {
  // Mode 1: Resource detail
  assignedTags?: Tag[];
  onAdd?: (tagId: string) => void;
  onRemove?: (tagId: string) => void;

  // Mode 2: Form selection
  selectedTagIds?: string[];
  onTagsChange?: (tagIds: string[]) => void;

  // Shared
  allTags: Tag[];
  readOnly?: boolean;
  onCreate?: (name: string, color: string) => Promise<Tag | null>;
  /**
   * Visual variant. `'default'` = the zinc-themed sidebar block with a "Tags"
   * header. `'bare'` = no header / no top border, chips use a unified
   * translucent-white tone (for use over a colored gradient, e.g. the mobile
   * audio screen).
   */
  variant?: 'default' | 'bare';
}
