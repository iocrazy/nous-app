import React, { createContext, useContext, useState, useCallback, useRef } from 'react';
import { AlertTriangle, Trash2, XCircle, Info, X } from 'lucide-react';

// ─── Types ──────────────────────────────────────────────

type ConfirmVariant = 'danger' | 'warning' | 'info';

interface ConfirmOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: ConfirmVariant;
  icon?: React.ReactNode;
}

interface ConfirmContextType {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

// ─── Context ────────────────────────────────────────────

const ConfirmContext = createContext<ConfirmContextType | null>(null);

export const useConfirm = (): ((options: ConfirmOptions) => Promise<boolean>) => {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm must be used within ConfirmProvider');
  return ctx.confirm;
};

// ─── Variant config ─────────────────────────────────────

const VARIANT_CONFIG: Record<ConfirmVariant, {
  icon: React.ReactNode;
  iconBg: string;
  buttonClass: string;
}> = {
  danger: {
    icon: <Trash2 size={20} className="text-red-400" />,
    iconBg: 'bg-red-500/10',
    buttonClass: 'bg-red-600 hover:bg-red-500 text-white',
  },
  warning: {
    icon: <AlertTriangle size={20} className="text-amber-400" />,
    iconBg: 'bg-amber-500/10',
    buttonClass: 'bg-amber-600 hover:bg-amber-500 text-white',
  },
  info: {
    icon: <Info size={20} className="text-indigo-400" />,
    iconBg: 'bg-indigo-500/10',
    buttonClass: 'bg-indigo-600 hover:bg-indigo-500 text-white',
  },
};

// ─── Provider ───────────────────────────────────────────

export const ConfirmProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, setState] = useState<(ConfirmOptions & { visible: boolean }) | null>(null);
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback((options: ConfirmOptions): Promise<boolean> => {
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
      setState({ ...options, visible: true });
    });
  }, []);

  const handleResult = useCallback((result: boolean) => {
    resolveRef.current?.(result);
    resolveRef.current = null;
    setState(null);
  }, []);

  const variant = state?.variant ?? 'danger';
  const config = VARIANT_CONFIG[variant];

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}

      {/* Overlay + Dialog */}
      {state?.visible && (
        <div className="fixed inset-0 z-[9999] flex items-center justify-center">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200"
            onClick={() => handleResult(false)}
          />

          {/* Dialog */}
          <div className="relative w-full max-w-md mx-4 bg-ink-900 border border-ink-700 rounded-2xl shadow-2xl animate-in fade-in zoom-in-95 duration-200">
            {/* Close */}
            <button
              onClick={() => handleResult(false)}
              className="absolute top-4 right-4 text-ink-500 hover:text-ink-300 transition-colors"
            >
              <X size={18} />
            </button>

            {/* Header */}
            <div className="flex items-center gap-3 px-6 pt-6 pb-2">
              <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${config.iconBg}`}>
                {state.icon || config.icon}
              </div>
              <h3 className="text-base font-semibold text-ink-50">{state.title}</h3>
            </div>

            {/* Body */}
            <div className="px-6 py-3">
              <p className="text-sm text-ink-400 leading-relaxed">{state.message}</p>
            </div>

            {/* Actions */}
            <div className="flex gap-3 px-6 pb-6 pt-2">
              <button
                onClick={() => handleResult(false)}
                className="flex-1 px-4 py-2.5 text-sm font-medium text-ink-300 bg-ink-800 hover:bg-ink-700 border border-ink-700 rounded-xl transition-colors"
              >
                {state.cancelLabel ?? 'Cancel'}
              </button>
              <button
                onClick={() => handleResult(true)}
                className={`flex-1 px-4 py-2.5 text-sm font-medium rounded-xl transition-colors ${config.buttonClass}`}
              >
                {state.confirmLabel ?? 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
};
