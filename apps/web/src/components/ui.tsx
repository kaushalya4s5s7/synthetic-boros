"use client";
import { useState, type ReactNode } from "react";

export function Stat({ fig, label, src, blue }: { fig: ReactNode; label: string; src?: string; blue?: boolean }) {
  return (
    <div className="stat">
      <div className={`fig ${blue ? "blue" : ""}`}>{fig}</div>
      <div className="lbl">{label}</div>
      {src && <div className="src">{src}</div>}
    </div>
  );
}

export function PageHead({ title, support, children }: { title: string; support?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1 className="title">{title}</h1>
        {support && <p className="support">{support}</p>}
      </div>
      {children && <div className="btn-row">{children}</div>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="field-dots empty">
      <div className="card">
        <div style={{ fontWeight: 500, fontSize: 15 }}>{title}</div>
        {children && <div className="caption" style={{ fontSize: 14, marginTop: 6 }}>{children}</div>}
      </div>
    </div>
  );
}

/** Confirm dialog: the user sees exactly what will happen before it happens. */
export function Confirm({
  title, children, confirmLabel, onConfirm, onClose,
}: {
  title: string; children: ReactNode; confirmLabel: string;
  onConfirm: () => Promise<void>; onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {children}
        {err && <div className="banner" style={{ marginTop: 12 }}><strong>Not done.</strong> {err}</div>}
        <div className="btn-row" style={{ justifyContent: "flex-end", marginTop: 16 }}>
          <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            className="btn primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await onConfirm();
                onClose();
              } catch (e) {
                setErr((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function Loading() {
  return <p className="muted">Loading…</p>;
}
