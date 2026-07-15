import {
  Children,
  forwardRef,
  isValidElement,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ChangeEvent,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown, Search, X } from 'lucide-react';
import {
  UI_CONTENT_OVERLAY_INSET_CLASS,
  UI_DIALOG_TRANSITION_MS,
  UI_POPOVER_TRANSITION_MS,
} from './motion';
import { useDialogTransition } from './useDialogTransition';

type ButtonVariant = 'primary' | 'muted' | 'ghost';

type ButtonSize = 'sm' | 'md';

interface UiButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

interface UiIconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean;
}

interface UiChipButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean;
}

interface UiCheckboxProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> {
  checked: boolean;
  onCheckedChange?: (checked: boolean) => void;
}

interface UiSelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  /**
   * Fully replaces the default trigger surface/size classes (border, bg, height,
   * padding, text). Structural layout + open/disabled behaviour are kept. Use for
   * contexts with their own visual language (e.g. canvas pill controls) that still
   * want the shared portal menu, keyboard nav, and checkmark. `className` is always
   * appended after, so callers can still tweak layout (width, margins).
   */
  triggerClassName?: string;
  /**
   * Show a text search box at the top of the menu that filters options by label.
   * Defaults to auto (on when there are more than 7 options) — pass `false` to
   * force it off, or `true` to always show it.
   */
  searchable?: boolean;
}

interface UiSelectOptionItem {
  kind: 'option';
  value: string;
  label: ReactNode;
  disabled: boolean;
  /** Muted second line under the label (nous-style two-line rows). */
  description?: string;
  /** Colored status dot on the leading edge (e.g. per-provider hue). */
  dot?: string;
  /** Tri-state: undefined = no load semantics; true/false → green "loaded"
   *  dot + enables the "Only loaded" filter at the top of the menu. */
  loaded?: boolean;
}

interface UiSelectGroupItem {
  kind: 'group';
  key: string;
  label: ReactNode;
}

type UiSelectItem = UiSelectOptionItem | UiSelectGroupItem;

interface UiModalProps {
  isOpen: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  widthClassName?: string;
  containerClassName?: string;
}

function resolveButtonVariant(variant: ButtonVariant): string {
  if (variant === 'primary') {
    return 'bg-accent text-white hover:bg-accent/85';
  }

  if (variant === 'ghost') {
    return 'bg-transparent text-text-dark hover:bg-[rgba(15,23,42,0.08)] dark:hover:bg-bg-dark/70';
  }

  return 'bg-[rgba(15,23,42,0.08)] text-text-dark hover:bg-[rgba(15,23,42,0.14)] dark:bg-bg-dark/80 dark:hover:bg-bg-dark';
}

function resolveButtonSize(size: ButtonSize): string {
  return size === 'sm' ? 'h-8 px-3 text-xs' : 'h-10 px-3.5 text-sm';
}

export function UiButton({
  className = '',
  variant = 'muted',
  size = 'md',
  ...props
}: UiButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${resolveButtonVariant(variant)} ${resolveButtonSize(size)} ${className}`}
      {...props}
    />
  );
}

export function UiIconButton({ className = '', active = false, ...props }: UiIconButtonProps) {
  return (
    <button
      className={`inline-flex h-10 w-10 items-center justify-center border ui-field transition-colors ${active ? 'border-accent/45 bg-accent/18 text-text-dark' : 'text-text-muted hover:bg-[rgba(15,23,42,0.08)] dark:hover:bg-bg-dark'} ${className}`}
      {...props}
    />
  );
}

export const UiChipButton = forwardRef<HTMLButtonElement, UiChipButtonProps>(
  ({ className = '', active = false, ...props }, ref) => (
    <button
      ref={ref}
      className={`inline-flex h-10 items-center gap-2 border ui-field px-3 text-sm transition-colors ${active ? 'border-accent/45 bg-accent/15 text-text-dark' : 'text-text-dark hover:bg-[rgba(15,23,42,0.08)] dark:hover:bg-bg-dark'} ${className}`}
      {...props}
    />
  )
);

UiChipButton.displayName = 'UiChipButton';

export function UiPanel({ className = '', ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`border ui-panel ${className}`}
      {...props}
    />
  );
}

export function UiTextArea({ className = '', ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={`w-full resize-none border ui-field px-3 py-2.5 text-sm text-text-dark outline-none transition-colors placeholder:text-text-muted/70 focus:border-accent ${className}`}
      {...props}
    />
  );
}

export const UiTextAreaField = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className = '', ...props }, ref) => (
    <textarea
      ref={ref}
      className={`w-full resize-none border ui-field px-3 py-2.5 text-sm text-text-dark outline-none transition-colors placeholder:text-text-muted/70 focus:border-accent ${className}`}
      {...props}
    />
  )
);

UiTextAreaField.displayName = 'UiTextAreaField';

export const UiInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className = '', ...props }, ref) => (
    <input
      ref={ref}
      className={`w-full border ui-field px-3 py-2 text-sm text-text-dark outline-none transition-colors placeholder:text-text-muted/70 focus:border-accent ${className}`}
      {...props}
    />
  )
);

UiInput.displayName = 'UiInput';

export const UiCheckbox = forwardRef<HTMLButtonElement, UiCheckboxProps>(
  ({ className = '', checked, onCheckedChange, onClick, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      role="checkbox"
      aria-checked={checked}
      className={`inline-flex h-5 w-5 items-center justify-center rounded border transition-colors ${
        checked
          ? 'border-accent/60 bg-accent/20 text-accent'
          : 'border-[rgba(255,255,255,0.2)] bg-bg-dark/60 text-transparent hover:border-[rgba(255,255,255,0.32)]'
      } ${className}`}
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) {
          onCheckedChange?.(!checked);
        }
      }}
      {...props}
    >
      <Check className="h-3.5 w-3.5" />
    </button>
  )
);

UiCheckbox.displayName = 'UiCheckbox';

export function UiSelect({
  className = '',
  triggerClassName,
  searchable,
  children,
  ...props
}: UiSelectProps) {
  const {
    value,
    defaultValue,
    onChange,
    onBlur,
    onFocus,
    disabled,
    name,
    autoFocus,
    'aria-label': ariaLabel,
    ...selectProps
  } = props;
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const hiddenSelectRef = useRef<HTMLSelectElement | null>(null);
  const listboxIdRef = useRef(`ui-select-${Math.random().toString(36).slice(2, 10)}`);
  const [isOpen, setIsOpen] = useState(false);
  const [onlyLoaded, setOnlyLoaded] = useState(false);
  const [query, setQuery] = useState('');
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const [menuStyle, setMenuStyle] = useState<{ left: number; top: number; width: number }>({
    left: 0,
    top: 0,
    width: 0,
  });
  const { shouldRender: shouldRenderMenu, isVisible: isMenuVisible } = useDialogTransition(
    isOpen,
    UI_POPOVER_TRANSITION_MS
  );
  const parsedItems = useMemo<UiSelectItem[]>(() => {
    const toOption = (child: unknown): UiSelectOptionItem | null => {
      if (!isValidElement(child) || child.type !== 'option') {
        return null;
      }

      const optionValue = child.props.value ?? child.props.children;
      const rawLoaded = child.props['data-loaded'];
      return {
        kind: 'option',
        value: String(optionValue ?? ''),
        label: child.props.children,
        disabled: Boolean(child.props.disabled),
        description: child.props['data-description'] || undefined,
        dot: child.props['data-dot'] || undefined,
        loaded:
          rawLoaded === undefined ? undefined : rawLoaded === true || rawLoaded === 'true',
      };
    };

    return Children.toArray(children).flatMap((child, index) => {
      if (isValidElement(child) && child.type === 'optgroup') {
        const groupOptions = Children.toArray(child.props.children)
          .map(toOption)
          .filter((option): option is UiSelectOptionItem => option !== null);
        if (groupOptions.length === 0) {
          return [];
        }
        const groupItem: UiSelectGroupItem = {
          kind: 'group',
          key: `group-${index}`,
          label: child.props.label ?? '',
        };
        return [groupItem, ...groupOptions];
      }

      const option = toOption(child);
      return option ? [option] : [];
    });
  }, [children]);
  const parsedOptions = useMemo<UiSelectOptionItem[]>(
    () => parsedItems.filter((item): item is UiSelectOptionItem => item.kind === 'option'),
    [parsedItems]
  );
  const initialValue = useMemo(() => {
    if (value != null) {
      return String(value);
    }

    if (defaultValue != null) {
      return String(defaultValue);
    }

    return parsedOptions.find((option) => !option.disabled)?.value ?? '';
  }, [defaultValue, parsedOptions, value]);
  const [uncontrolledValue, setUncontrolledValue] = useState(initialValue);
  const isControlled = value != null;
  const selectedValue = isControlled ? String(value) : uncontrolledValue;
  const selectedOption =
    parsedOptions.find((option) => option.value === selectedValue) ??
    parsedOptions.find((option) => !option.disabled) ??
    null;
  // Rich mode (nous parity): any option carrying a description / dot / load
  // state promotes the whole menu to two-line rows with a leading indicator.
  const hasLoadedInfo = parsedOptions.some((option) => option.loaded !== undefined);
  const loadedCount = parsedOptions.filter((option) => option.loaded).length;
  const richMode = parsedOptions.some(
    (option) => option.description || option.dot || option.loaded !== undefined
  );
  const showSearch = searchable ?? parsedOptions.length > 7;
  const shownItems = useMemo(() => {
    const q = query.trim().toLowerCase();
    const keepOption = (item: UiSelectOptionItem) => {
      if (onlyLoaded && hasLoadedInfo && !(item.loaded || item.value === selectedValue)) {
        return false;
      }
      if (q) {
        const hay = `${typeof item.label === 'string' ? item.label : item.value} ${
          item.description ?? ''
        }`.toLowerCase();
        return hay.includes(q);
      }
      return true;
    };
    const kept = parsedItems.filter((item) => item.kind !== 'option' || keepOption(item));
    // Drop group headers left with no options under them.
    return kept.filter((item, index) => {
      if (item.kind !== 'group') {
        return true;
      }
      const next = kept[index + 1];
      return next != null && next.kind === 'option';
    });
  }, [onlyLoaded, hasLoadedInfo, parsedItems, selectedValue, query]);

  useEffect(() => {
    if (!isControlled) {
      setUncontrolledValue(initialValue);
    }
  }, [initialValue, isControlled]);

  // autoFocus lands focus on the trigger (not the aria-hidden native select) on
  // mount — mirrors the native <select autoFocus> affordance for callers that
  // render this conditionally when a field should take focus (e.g. edit mode).
  useEffect(() => {
    if (autoFocus) {
      triggerRef.current?.focus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reset the search query when the menu closes; focus the search box on open.
  useEffect(() => {
    if (!isOpen) {
      setQuery('');
      return;
    }
    if (showSearch) {
      const id = window.setTimeout(() => searchInputRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
  }, [isOpen, showSearch]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const updatePosition = () => {
      const trigger = triggerRef.current;
      if (!trigger) {
        return;
      }

      const rect = trigger.getBoundingClientRect();
      const viewportHeight = window.innerHeight;
      const estimatedMenuHeight = Math.min(Math.max(parsedItems.length * 36 + 12, 60), 280);
      const openAbove = rect.bottom + 8 + estimatedMenuHeight > viewportHeight && rect.top > estimatedMenuHeight;
      setMenuStyle({
        left: rect.left,
        top: openAbove ? Math.max(8, rect.top - estimatedMenuHeight - 8) : rect.bottom + 8,
        width: rect.width,
      });
    };

    updatePosition();
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);
    return () => {
      window.removeEventListener('resize', updatePosition);
      window.removeEventListener('scroll', updatePosition, true);
    };
  }, [isOpen, parsedItems.length]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (triggerRef.current?.contains(target ?? null)) {
        return;
      }

      const menuElement = document.getElementById(listboxIdRef.current);
      if (menuElement?.contains(target ?? null)) {
        return;
      }

      setIsOpen(false);
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
        triggerRef.current?.focus();
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);

  const commitValue = (nextValue: string) => {
    if (!isControlled) {
      setUncontrolledValue(nextValue);
    }

    if (hiddenSelectRef.current) {
      hiddenSelectRef.current.value = nextValue;
    }

    onChange?.({
      target: { value: nextValue, name },
      currentTarget: { value: nextValue, name },
    } as ChangeEvent<HTMLSelectElement>);
  };

  const handleTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (disabled || parsedOptions.length === 0) {
      return;
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setIsOpen((current) => !current);
      return;
    }

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const enabledOptions = parsedOptions.filter((option) => !option.disabled);
      if (enabledOptions.length === 0) {
        return;
      }

      const currentIndex = enabledOptions.findIndex((option) => option.value === selectedValue);
      const fallbackIndex = event.key === 'ArrowDown' ? 0 : enabledOptions.length - 1;
      const nextIndex =
        currentIndex === -1
          ? fallbackIndex
          : (currentIndex + (event.key === 'ArrowDown' ? 1 : -1) + enabledOptions.length) %
            enabledOptions.length;
      commitValue(enabledOptions[nextIndex].value);
      setIsOpen(false);
    }
  };

  return (
    <div className="relative">
      <select
        ref={hiddenSelectRef}
        tabIndex={-1}
        aria-hidden="true"
        value={selectedValue}
        name={name}
        disabled={disabled}
        className="pointer-events-none absolute inset-0 opacity-0"
        onChange={(event) => commitValue(event.target.value)}
        {...selectProps}
      >
        {children}
      </select>
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={listboxIdRef.current}
        disabled={disabled}
        className={`group inline-flex items-center justify-between text-left outline-none transition-[border-color,background-color,box-shadow,color] disabled:cursor-not-allowed disabled:opacity-55 ${
          triggerClassName ??
          'h-9 w-full rounded-[8px] border border-ink-700 bg-ink-800 px-3 text-sm font-medium text-content hover:border-ink-600 focus-visible:border-ink-500 focus-visible:shadow-[0_0_0_2px_color-mix(in_srgb,var(--content-3)_34%,transparent)]'
        } ${className}`}
        onClick={() => {
          if (!disabled && parsedOptions.length > 0) {
            setIsOpen((current) => !current);
          }
        }}
        onKeyDown={handleTriggerKeyDown}
        onBlur={(event) => onBlur?.(event as never)}
        onFocus={(event) => onFocus?.(event as never)}
      >
        <span className="min-w-0 truncate pr-3">{selectedOption?.label ?? ''}</span>
        <span className="flex h-4 w-4 shrink-0 items-center justify-center text-content-3 transition-colors group-hover:text-content group-focus-visible:text-accent">
          <ChevronDown
            className={`h-3.5 w-3.5 transition-transform ${isOpen ? 'rotate-180' : ''}`}
            style={{ transitionDuration: `${UI_POPOVER_TRANSITION_MS}ms` }}
          />
        </span>
      </button>
      {shouldRenderMenu && typeof document !== 'undefined'
        ? createPortal(
            <div
              id={listboxIdRef.current}
              role="listbox"
              aria-label={ariaLabel}
              className={`fixed z-[140] overflow-hidden rounded-[8px] border border-ink-700 bg-card p-1 shadow-[0_12px_34px_rgba(0,0,0,0.22)] transition-[opacity,transform] ease-out ${
                isMenuVisible ? 'opacity-100 translate-y-0' : 'pointer-events-none opacity-0 -translate-y-1'
              }`}
              style={{
                left: menuStyle.left,
                top: menuStyle.top,
                width: Math.max(menuStyle.width, richMode || showSearch ? 240 : 0),
                maxHeight: 320,
                transitionDuration: `${UI_POPOVER_TRANSITION_MS}ms`,
              }}
            >
              {showSearch ? (
                <div className="mb-1 flex items-center gap-2 border-b border-ink-700 px-2.5 pb-2 pt-1">
                  <Search className="h-3.5 w-3.5 shrink-0 text-content-3" />
                  <input
                    ref={searchInputRef}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search…"
                    className="w-full bg-transparent text-sm text-content outline-none placeholder:text-content-3"
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        const first = shownItems.find(
                          (item) => item.kind === 'option' && !item.disabled
                        );
                        if (first && first.kind === 'option') {
                          commitValue(first.value);
                          setIsOpen(false);
                          triggerRef.current?.focus();
                        }
                      } else if (event.key === 'Escape') {
                        setIsOpen(false);
                        triggerRef.current?.focus();
                      }
                    }}
                  />
                </div>
              ) : null}
              <div className="ui-scrollbar max-h-[228px] overflow-y-auto">
                {hasLoadedInfo ? (
                  <button
                    type="button"
                    className="mb-1 flex w-full items-center gap-2 rounded-[6px] border border-ink-700 px-2.5 py-1.5 text-xs text-content transition-colors hover:bg-ink-700"
                    onClick={(event) => {
                      event.stopPropagation();
                      setOnlyLoaded((current) => !current);
                    }}
                  >
                    <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-500" />
                    <span className="flex-1 text-left">Only loaded</span>
                    <span className="tabular-nums text-content-3">{loadedCount}</span>
                    {onlyLoaded ? (
                      <Check className="h-3.5 w-3.5 shrink-0 text-[color:var(--accent-text)]" />
                    ) : null}
                  </button>
                ) : null}
                {shownItems.length === 0 ? (
                  <div className="px-3 py-6 text-center text-xs text-content-3">No matches</div>
                ) : null}
                {shownItems.map((item) => {
                  if (item.kind === 'group') {
                    return (
                      <div
                        key={item.key}
                        role="presentation"
                        className="truncate px-2.5 pt-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wide text-content-3 first:pt-1"
                      >
                        {item.label}
                      </div>
                    );
                  }

                  const isSelected = item.value === selectedValue;
                  return (
                    <button
                      key={item.value}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      disabled={item.disabled}
                      className={`flex w-full items-start gap-2.5 rounded-[6px] px-2.5 py-2 text-left text-sm transition-colors ${
                        item.disabled
                          ? 'cursor-not-allowed opacity-40'
                          : isSelected
                            ? 'bg-ink-700 font-medium text-content'
                            : 'text-content hover:bg-ink-700'
                      }`}
                      onClick={() => {
                        if (item.disabled) {
                          return;
                        }
                        commitValue(item.value);
                        setIsOpen(false);
                        triggerRef.current?.focus();
                      }}
                    >
                      {richMode ? (
                        <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
                          {isSelected ? (
                            <Check className="h-3.5 w-3.5 text-[color:var(--accent-text)]" />
                          ) : item.loaded ? (
                            <span className="h-2 w-2 rounded-full bg-emerald-500" title="Loaded" />
                          ) : item.dot ? (
                            <span className="h-2 w-2 rounded-full" style={{ background: item.dot }} />
                          ) : null}
                        </span>
                      ) : null}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">{item.label}</span>
                        {item.description ? (
                          <span
                            aria-hidden="true"
                            className="mt-0.5 block truncate text-[11px] font-normal text-content-3"
                          >
                            {item.description}
                          </span>
                        ) : null}
                      </span>
                      {!richMode && isSelected ? (
                        <Check className="ml-3 mt-0.5 h-3.5 w-3.5 shrink-0 text-[color:var(--accent-text)]" />
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </div>,
            document.body
          )
        : null}
    </div>
  );
}

export function UiModal({
  isOpen,
  title,
  onClose,
  children,
  footer,
  widthClassName = 'w-[460px]',
  containerClassName = '',
}: UiModalProps) {
  const { shouldRender, isVisible } = useDialogTransition(isOpen, UI_DIALOG_TRANSITION_MS);

  if (!shouldRender) {
    return null;
  }

  return (
    <div className={`fixed ${UI_CONTENT_OVERLAY_INSET_CLASS} z-50 flex items-center justify-center ${containerClassName}`}>
      <div
        className={`absolute inset-0 bg-black/55 transition-opacity duration-200 ${isVisible ? 'opacity-100' : 'opacity-0'}`}
        onClick={onClose}
      />
      <UiPanel
        className={`relative transition-opacity duration-200 ${isVisible ? 'opacity-100' : 'opacity-0'} ${widthClassName}`}
      >
        <div className="flex items-center justify-between border-b border-[rgba(255,255,255,0.1)] px-4 py-3">
          <h2 className="text-sm font-medium text-text-dark">{title}</h2>
          <UiIconButton className="h-8 w-8" onClick={onClose}>
            <X className="h-4 w-4" />
          </UiIconButton>
        </div>

        <div className="px-4 py-4">{children}</div>

        {footer && (
          <div className="flex justify-end gap-2 border-t border-[rgba(255,255,255,0.1)] px-4 py-3">
            {footer}
          </div>
        )}
      </UiPanel>
    </div>
  );
}
