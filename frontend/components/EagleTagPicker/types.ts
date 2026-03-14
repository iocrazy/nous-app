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
}
