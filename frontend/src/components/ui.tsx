import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

/* ------------------------------------------------------------------ toast */
type Toast = { id: number; text: string; tone: "ok" | "err" };
type ToastCtx = { push: (text: string, tone?: "ok" | "err") => void };

const ToastContext = createContext<ToastCtx>({ push: () => {} });

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((prev) => [...prev, { id, text, tone }]);
    window.setTimeout(() => setItems((prev) => prev.filter((item) => item.id !== id)), 4200);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-wrap">
        {items.map((item) => (
          <div key={item.id} className={`toast ${item.tone}`}>
            {item.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastCtx {
  return useContext(ToastContext);
}

/* ---------------------------------------------------------------- panels */
export function Panel({
  title,
  right,
  children,
  bodyClass = "",
  className = "",
}: {
  title?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  bodyClass?: string;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title !== undefined && (
        <header className="panel-head">
          <div className="panel-title">
            <span className="dot" />
            {title}
          </div>
          {right}
        </header>
      )}
      <div className={`panel-body ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, suffix, hint }: { label: string; value: React.ReactNode; suffix?: string; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">
        {value}
        {suffix && <small>{suffix}</small>}
      </div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Meter({ value, tone = "" }: { value: number; tone?: string }) {
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <div className={`meter ${tone}`}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">
        <span>{label}</span>
        {hint && <span className="hint">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

export function Tabs<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { key: T; label: string }[];
  onChange: (key: T) => void;
}) {
  return (
    <div className="tabs">
      {options.map((option) => (
        <button
          key={option.key}
          className={`tab ${value === option.key ? "active" : ""}`}
          onClick={() => onChange(option.key)}
          type="button"
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner() {
  return <span className="spinner" />;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; loading: boolean; error: string; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn()
      .then((value) => {
        if (alive) {
          setData(value);
          setError("");
        }
      })
      .catch((err: Error) => {
        if (alive) setError(err.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, loading, error, reload: () => setNonce((n) => n + 1) };
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "-";
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}
