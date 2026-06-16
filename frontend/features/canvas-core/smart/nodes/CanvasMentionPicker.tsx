/**
 * @-mention resource picker for SmartMode canvas prompt nodes.
 *
 * A thin positioned wrapper around ResourcePickerSuggestion — reuses the
 * identical dropdown UI from the chat composer without duplicating it.
 * PromptNodeView owns all search state (useResourceSearch + activeKind) and
 * passes resolved results in; this component handles only presentation and
 * positioning relative to the textarea.
 *
 * Positioning: `absolute bottom-full left-0` places the popover above the
 * containing `.relative` div (the prompt node's body section). The picker
 * is layered at z-50 so it floats above other canvas nodes.
 *
 * Focus preservation: `onMouseDown={e.preventDefault()}` keeps focus in the
 * textarea when the user clicks a result row, so the `onBlur` → closePicker
 * that would normally dismiss the popover does not fire before the click
 * registers on the item.
 */

import React, { type ComponentProps } from 'react';

import { ResourcePickerSuggestion } from '../../../../components/chat/ResourcePickerSuggestion';

type SuggestionProps = ComponentProps<typeof ResourcePickerSuggestion>;

export function CanvasMentionPicker(props: SuggestionProps): React.ReactElement {
  return (
    <div
      className="absolute bottom-full left-0 z-50 mb-1"
      onMouseDown={(e) => e.preventDefault()}
      data-testid="canvas-mention-picker"
    >
      <ResourcePickerSuggestion {...props} />
    </div>
  );
}
