
import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import { X, CheckCircle2, AlertCircle, Info } from 'lucide-react';

type ToastType = 'success' | 'error' | 'info';

interface Toast {
  id: string;
  message: string;
  type: ToastType;
  duration: number;
}

interface ToastContextValue {
  addToast: (message: string, type?: ToastType, duration?: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export const useToast = (): ToastContextValue => {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
};

const toastStyles: Record<ToastType, { icon: React.ElementType; border: string; bg: string; text: string; iconColor: string }> = {
  success: { icon: CheckCircle2, border: 'border-emerald-500/30', bg: 'bg-emerald-500/10', text: 'text-emerald-300', iconColor: 'text-emerald-400' },
  error:   { icon: AlertCircle,  border: 'border-red-500/30',     bg: 'bg-red-500/10',     text: 'text-red-300',     iconColor: 'text-red-400' },
  info:    { icon: Info,         border: 'border-sky-500/30',     bg: 'bg-sky-500/10',     text: 'text-sky-300',     iconColor: 'text-sky-400' },
};

const ToastItem: React.FC<{ toast: Toast; onDismiss: (id: string) => void }> = ({ toast, onDismiss }) => {
  const style = toastStyles[toast.type];
  const Icon = style.icon;

  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), toast.duration);
    return () => clearTimeout(timer);
  }, [toast.id, toast.duration, onDismiss]);

  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 rounded-lg border ${style.border} ${style.bg} backdrop-blur-md shadow-lg max-w-sm animate-in slide-in-from-right-5 fade-in duration-300`}
    >
      <Icon size={18} className={`${style.iconColor} shrink-0 mt-0.5`} />
      <p className={`text-sm ${style.text} flex-1`}>{toast.message}</p>
      <button
        onClick={() => onDismiss(toast.id)}
        className="text-zinc-500 hover:text-zinc-300 shrink-0"
      >
        <X size={14} />
      </button>
    </div>
  );
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counterRef = useRef(0);

  const addToast = useCallback((message: string, type: ToastType = 'info', duration = 5000) => {
    const id = `toast-${++counterRef.current}-${Date.now()}`;
    setToasts(prev => [...prev, { id, message, type, duration }]);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      {/* Toast container - fixed bottom-right */}
      {toasts.length > 0 && (
        <div className="fixed bottom-6 right-6 z-[200] flex flex-col gap-2">
          {toasts.map(toast => (
            <ToastItem key={toast.id} toast={toast} onDismiss={dismissToast} />
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
};
