import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

/* Toasts: brief, polite confirmations. Errors use Alert/ErrorState instead —
 * toasts never carry the only copy of an error message. */

export type ToastTone = "success" | "info" | "warning";

export interface ToastInput {
  title: string;
  body?: string;
  tone?: ToastTone;
  durationMs?: number;
}

interface ToastRecord extends Required<Omit<ToastInput, "body">> {
  id: number;
  body?: string;
}

interface ToastContextValue {
  toast: (input: ToastInput) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const toast = useCallback(
    (input: ToastInput) => {
      const id = nextId.current;
      nextId.current += 1;
      const record: ToastRecord = {
        id,
        title: input.title,
        body: input.body,
        tone: input.tone ?? "success",
        durationMs: input.durationMs ?? 4200,
      };
      setToasts((current) => [...current.slice(-3), record]);
      window.setTimeout(() => dismiss(id), record.durationMs);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {createPortal(
        <div className="tl-toast-region" aria-live="polite" aria-atomic="false">
          {toasts.map((item) => (
            <div key={item.id} role="status" className={["tl-toast", `tl-toast-${item.tone}`].join(" ")}>
              <div className="tl-toast-text">
                <p className="tl-toast-title">{item.title}</p>
                {item.body ? <p className="tl-toast-body">{item.body}</p> : null}
              </div>
              <button
                type="button"
                className="tl-toast-close"
                aria-label={`Dismiss: ${item.title}`}
                onClick={() => dismiss(item.id)}
              >
                ×
              </button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside <ToastProvider>.");
  return context;
}
