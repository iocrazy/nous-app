import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Save, ArrowLeft, Trash2, Copy, Download, Upload, Settings, Pencil } from 'lucide-react';

interface EditorTopBarProps {
  projectName: string;
  onBack: () => void;
  onExport?: () => void;
  onImport?: () => void;
  onSave?: () => void;
  onDuplicate?: () => void;
  onDelete?: () => void;
  onRename?: (newName: string) => void;
  saving?: boolean;
  children?: React.ReactNode;
}

export function EditorTopBar({
  projectName,
  onBack,
  onExport,
  onImport,
  onSave,
  onDuplicate,
  onDelete,
  onRename,
  saving = false,
  children,
}: EditorTopBarProps) {
  const [logoMenuOpen, setLogoMenuOpen] = useState(false);
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(projectName);

  const logoMenuRef = useRef<HTMLDivElement>(null);
  const logoButtonRef = useRef<HTMLButtonElement>(null);
  const projectMenuRef = useRef<HTMLDivElement>(null);
  const projectTriggerRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Sync rename value when projectName prop changes
  useEffect(() => {
    setRenameValue(projectName);
  }, [projectName]);

  // Focus input when rename mode activates
  useEffect(() => {
    if (isRenaming && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [isRenaming]);

  // Close logo menu on outside click
  useEffect(() => {
    if (!logoMenuOpen) return;

    function handleClickOutside(e: MouseEvent) {
      if (
        logoMenuRef.current &&
        !logoMenuRef.current.contains(e.target as Node) &&
        logoButtonRef.current &&
        !logoButtonRef.current.contains(e.target as Node)
      ) {
        setLogoMenuOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [logoMenuOpen]);

  // Close project menu on outside click
  useEffect(() => {
    if (!projectMenuOpen) return;

    function handleClickOutside(e: MouseEvent) {
      if (
        projectMenuRef.current &&
        !projectMenuRef.current.contains(e.target as Node) &&
        projectTriggerRef.current &&
        !projectTriggerRef.current.contains(e.target as Node)
      ) {
        setProjectMenuOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [projectMenuOpen]);

  // Cmd+S shortcut
  useEffect(() => {
    if (!onSave) return;

    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        if (!saving) onSave?.();
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onSave, saving]);

  function handleLogoMenuAction(action: () => void) {
    setLogoMenuOpen(false);
    action();
  }

  function handleProjectMenuAction(action: () => void) {
    setProjectMenuOpen(false);
    action();
  }

  function handleRenameStart() {
    setProjectMenuOpen(false);
    setRenameValue(projectName);
    setIsRenaming(true);
  }

  function handleRenameCommit() {
    const trimmed = renameValue.trim();
    if (trimmed && trimmed !== projectName) {
      onRename?.(trimmed);
    }
    setIsRenaming(false);
  }

  function handleRenameKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      handleRenameCommit();
    } else if (e.key === 'Escape') {
      setIsRenaming(false);
    }
  }

  return (
    <div className="h-11 flex items-center px-3 gap-1 bg-ink-900 border-b border-ink-800 flex-shrink-0 select-none">
      {/* Logo + dropdown trigger */}
      <div className="relative">
        <button
          ref={logoButtonRef}
          type="button"
          onClick={() => setLogoMenuOpen((v) => !v)}
          className={`flex items-center gap-1 rounded-lg px-2 py-1.5 transition-colors hover:bg-ink-800 ${logoMenuOpen ? 'bg-ink-800' : ''}`}
        >
          {/* MediaHub logo mark — simple spark shape */}
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            className="text-indigo-500 flex-shrink-0"
          >
            <path
              d="M12 2L9.5 9.5H2L7.75 13.75L5.5 21L12 16.75L18.5 21L16.25 13.75L22 9.5H14.5L12 2Z"
              fill="currentColor"
            />
          </svg>
          <ChevronDown
            size={12}
            className={`text-ink-600 ml-0.5 transition-transform ${logoMenuOpen ? 'rotate-180' : ''}`}
          />
        </button>

        {/* Logo dropdown menu — app-level actions */}
        {logoMenuOpen && (
          <div
            ref={logoMenuRef}
            className="absolute top-full left-0 mt-1 w-52 bg-ink-800 border border-ink-700 rounded-lg shadow-xl z-50 py-1 text-sm"
          >
            <MenuItem
              icon={<ArrowLeft size={14} />}
              label="Back to project"
              onClick={() => handleLogoMenuAction(onBack)}
            />

            <div className="my-1 border-t border-ink-700" />

            <MenuItem
              icon={<Settings size={14} />}
              label="Preferences..."
              onClick={() => setLogoMenuOpen(false)}
            />
          </div>
        )}
      </div>

      {/* Divider between logo and project name */}
      <div className="w-px h-5 bg-ink-700 mx-2" />

      {/* Project name + dropdown trigger */}
      <div className="relative">
        {isRenaming ? (
          <input
            ref={renameInputRef}
            type="text"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onBlur={handleRenameCommit}
            onKeyDown={handleRenameKeyDown}
            className="px-2 py-1 text-sm text-ink-100 bg-ink-800 border border-indigo-500 rounded outline-none w-48 max-w-xs"
          />
        ) : (
          <div
            ref={projectTriggerRef}
            onClick={() => setProjectMenuOpen((v) => !v)}
            className={`flex items-center gap-1 px-2 py-1 rounded hover:bg-ink-800 cursor-pointer transition-colors ${projectMenuOpen ? 'bg-ink-800' : ''}`}
          >
            <span className="text-sm font-medium text-ink-200 truncate max-w-xs">{projectName}</span>
            <ChevronDown
              size={14}
              className={`text-ink-500 flex-shrink-0 transition-transform ${projectMenuOpen ? 'rotate-180' : ''}`}
            />
          </div>
        )}

        {/* Project dropdown menu — project-level actions */}
        {projectMenuOpen && !isRenaming && (
          <div
            ref={projectMenuRef}
            className="absolute top-full left-0 mt-1 w-56 bg-ink-800 border border-ink-700 rounded-lg shadow-xl py-1 z-50"
          >
            <PlainMenuItem
              label="Rename"
              icon={<Pencil size={14} />}
              onClick={handleRenameStart}
            />
            <PlainMenuItem
              label="Duplicate"
              icon={<Copy size={14} />}
              onClick={() => handleProjectMenuAction(() => onDuplicate?.())}
            />

            <div className="border-t border-ink-700 my-1" />

            <PlainMenuItem
              label="Export..."
              shortcut="⌃⇧E"
              icon={<Download size={14} />}
              onClick={() => handleProjectMenuAction(() => onExport?.())}
            />
            <PlainMenuItem
              label="Import..."
              icon={<Upload size={14} />}
              onClick={() => handleProjectMenuAction(() => onImport?.())}
            />

            <div className="border-t border-ink-700 my-1" />

            <PlainMenuItem
              label="Move to trash"
              icon={<Trash2 size={14} />}
              onClick={() => handleProjectMenuAction(() => onDelete?.())}
              danger
            />
          </div>
        )}
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Right slot */}
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}

// ─── Logo Menu Item (with icon) ───────────────────────────────────────────────

function MenuItem({
  icon,
  label,
  shortcut,
  onClick,
  disabled = false,
}: {
  icon: React.ReactNode;
  label: string;
  shortcut?: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors text-sm ${
        disabled
          ? 'text-ink-600 cursor-not-allowed'
          : 'text-ink-300 hover:bg-ink-700 hover:text-ink-50'
      }`}
    >
      <span className="text-ink-500 flex-shrink-0">{icon}</span>
      <span className="flex-1">{label}</span>
      {shortcut && (
        <span className="text-xs text-ink-600 ml-auto">{shortcut}</span>
      )}
    </button>
  );
}

// ─── Project Menu Item (Figma style, text-only with optional icon) ────────────

function PlainMenuItem({
  label,
  shortcut,
  onClick,
  danger = false,
  icon,
}: {
  label: string;
  shortcut?: string;
  onClick: () => void;
  danger?: boolean;
  icon?: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full flex items-center justify-between px-3 py-1.5 text-sm cursor-pointer transition-colors ${
        danger
          ? 'text-red-400 hover:text-red-300 hover:bg-red-900/20'
          : 'text-ink-300 hover:bg-ink-700 hover:text-ink-50'
      }`}
    >
      <span className="flex items-center gap-2">
        {icon && (
          <span className={danger ? 'text-red-400' : 'text-ink-500'}>{icon}</span>
        )}
        {label}
      </span>
      {shortcut && (
        <span className="text-xs text-ink-500">{shortcut}</span>
      )}
    </button>
  );
}
