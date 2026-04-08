import { useState, useRef, useEffect } from 'react';
import { ChevronDown, ArrowLeft, Upload, Download, Save } from 'lucide-react';

interface EditorTopBarProps {
  projectName: string;
  onBack: () => void;
  onExport?: () => void;
  onImport?: () => void;
  onSave?: () => void;
  saving?: boolean;
  children?: React.ReactNode;
}

export function EditorTopBar({
  projectName,
  onBack,
  onExport,
  onImport,
  onSave,
  saving = false,
  children,
}: EditorTopBarProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;

    function handleClickOutside(e: MouseEvent) {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        buttonRef.current &&
        !buttonRef.current.contains(e.target as Node)
      ) {
        setMenuOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [menuOpen]);

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

  function handleMenuAction(action: () => void) {
    setMenuOpen(false);
    action();
  }

  return (
    <div className="h-11 flex items-center px-3 gap-3 bg-zinc-900 border-b border-zinc-800 flex-shrink-0 select-none">
      {/* Logo + dropdown trigger */}
      <div className="relative">
        <button
          ref={buttonRef}
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className="flex items-center gap-1 px-2 py-1.5 rounded-md hover:bg-zinc-800 transition-colors"
        >
          {/* MediaHub logo mark — simple spark shape */}
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            className="text-indigo-400 flex-shrink-0"
          >
            <path
              d="M12 2L9.5 9.5H2L7.75 13.75L5.5 21L12 16.75L18.5 21L16.25 13.75L22 9.5H14.5L12 2Z"
              fill="currentColor"
            />
          </svg>
          <ChevronDown
            size={12}
            className={`text-zinc-500 transition-transform ${menuOpen ? 'rotate-180' : ''}`}
          />
        </button>

        {/* Dropdown menu */}
        {menuOpen && (
          <div
            ref={menuRef}
            className="absolute top-full left-0 mt-1 w-52 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl z-50 py-1 text-sm"
          >
            <MenuItem
              icon={<ArrowLeft size={14} />}
              label="Back to project"
              onClick={() => handleMenuAction(onBack)}
            />

            {(onImport || onExport) && (
              <div className="my-1 border-t border-zinc-700" />
            )}

            {onImport && (
              <MenuItem
                icon={<Upload size={14} />}
                label="Import..."
                onClick={() => handleMenuAction(onImport)}
              />
            )}

            {onExport && (
              <MenuItem
                icon={<Download size={14} />}
                label="Export..."
                onClick={() => handleMenuAction(onExport)}
              />
            )}

            {onSave && (
              <>
                <div className="my-1 border-t border-zinc-700" />
                <MenuItem
                  icon={<Save size={14} />}
                  label="Save"
                  shortcut="⌘S"
                  onClick={() => handleMenuAction(onSave)}
                  disabled={saving}
                />
              </>
            )}
          </div>
        )}
      </div>

      {/* Project name */}
      <span className="text-sm text-zinc-300 truncate max-w-xs">{projectName}</span>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Right slot */}
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}

// ─── Menu Item ────────────────────────────────────────────────────────────────

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
      className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors ${
        disabled
          ? 'text-zinc-600 cursor-not-allowed'
          : 'text-zinc-300 hover:bg-zinc-700 hover:text-white'
      }`}
    >
      <span className="text-zinc-500 flex-shrink-0">{icon}</span>
      <span className="flex-1">{label}</span>
      {shortcut && (
        <span className="text-xs text-zinc-600 ml-auto">{shortcut}</span>
      )}
    </button>
  );
}
