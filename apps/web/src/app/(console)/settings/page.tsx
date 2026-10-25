"use client";
import { useEffect, useState } from "react";
import { api, type Settings } from "@/lib/api";
import { useLive } from "@/components/Live";
import { Loading, PageHead } from "@/components/ui";

interface FieldDef {
  key: string;
  label: string;
  hint: string;
  min?: number;
  max?: number;
  step?: number;
  /** stored as a fraction, shown as % */
  asPct?: boolean;
}

const ASSETS = [
  { u: "XAUT", label: "Tether Gold (XAUT) · RWA" },
  { u: "BTC", label: "Bitcoin (BTC)" },
];

const FIELDS: FieldDef[] = [
  { key: "capital_usd", label: "Capital to use (USD)", hint: "Margin for all three legs plus the reserve must fit inside this.", min: 1, step: 1 },
  { key: "leverage", label: "Perp leverage", hint: "Delta defaults to 200x on mainnet (10x on testnet). The bot always sets this value before entering.", min: 1, max: 5, step: 0.5 },
  { key: "reserve_pct", label: "Reserve for margin top-ups", hint: "Held back to top up a leg when BTC moves hard. 10% survived every stress path at 5x.", min: 0, max: 50, step: 1, asPct: true },
  { key: "max_fee_ratio", label: "Fee guard: max costs as share of funding edge", hint: "Entry + exit fees + spreads must stay below this share of the expected funding gain.", min: 5, max: 100, step: 5, asPct: true },
  { key: "min_net_edge_pct", label: "Minimum net edge (% of notional)", hint: "After all costs, over the whole hold.", min: 0, max: 2, step: 0.01 },
  { key: "max_spread_cost_pct", label: "Max option spread cost (% of notional)", hint: "Skip pairs whose half-spreads are wider than this.", min: 0, max: 1, step: 0.01 },
  { key: "exit_avg24_apr", label: "Exit when 24h average funding below (%/yr)", hint: "Backtested best: 0. Single negative hours are usually noise.", min: -60, max: 20, step: 0.5 },
  { key: "exit_now_apr", label: "Emergency exit when funding below (%/yr)", hint: "Immediate exit on a deep flip.", min: -60, max: 0, step: 1 },
  { key: "hard_stop_pct", label: "Hard stop (% of notional)", hint: "Closes everything if the position loses this much.", min: -5, max: 0, step: 0.05 },
  { key: "min_days", label: "Shortest expiry (days)", hint: "Shorter holds never covered their costs in the backtest.", min: 1, max: 90, step: 1 },
  { key: "max_days", label: "Longest expiry (days)", hint: "", min: 1, max: 120, step: 1 },
];

const PRESETS: { label: string; apr: number | null; note: string }[] = [
  { label: "Normal 10.95%", apr: 10.95, note: "baseline" },
  { label: "Thin 5%", apr: 5, note: "below fixed: hold" },
  { label: "Flip −10%", apr: -10, note: "countdown to exit" },
  { label: "Crash −30%", apr: -30, note: "emergency exit" },
  { label: "Clear", apr: null, note: "real data" },
];

export default function SettingsPage() {
  const { state, refresh, toast } = useLive();
  const [s, setS] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.settings()
      .then((v) => {
        setS(v);
        const d: Record<string, string> = {};
        for (const f of FIELDS) {
          const raw = v[f.key];
          if (typeof raw === "number") d[f.key] = String(f.asPct ? +(raw * 100).toFixed(4) : raw);
        }
        setDraft(d);
      })
      .catch((e) => setErr((e as Error).message));
  }, []);

  const running = state?.bot.mode === "running";
  const auto = state?.bot.auto_enter ?? false;
  const override = state?.market.override_apr ?? null;

  async function save() {
    if (!s) return;
    setSaving(true);
    setErr(null);
    try {
      const out: Settings = { ...s };
      for (const f of FIELDS) {
        if (draft[f.key] === undefined || draft[f.key] === "") continue;
        const n = Number(draft[f.key]);
        if (!Number.isFinite(n)) throw new Error(`${f.label}: not a number`);
        if (f.min !== undefined && n < f.min) throw new Error(`${f.label}: at least ${f.min}`);
        if (f.max !== undefined && n > f.max) throw new Error(`${f.label}: at most ${f.max}`);
        out[f.key] = f.asPct ? n / 100 : n;
      }
      const saved = await api.saveSettings(out);
      setS(saved ?? out);
      toast("Settings saved. They apply from the next tick.");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="stack">
      <PageHead title="Settings" support="Risk limits the bot enforces on every tick. Every value here was chosen from the backtest and the live testnet checks; change them with care." />

      <div className="panel">
        <h2 className="section-title">Bot</h2>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <label className="toggle">
            <input
              type="checkbox"
              checked={auto}
              onChange={async (e) => {
                await api.start(e.target.checked);
                toast(e.target.checked ? "Auto-enter on: the bot enters when an offer passes" : "Auto-enter off: you approve each entry");
                refresh();
              }}
            />
            Auto-enter when an offer passes every filter
          </label>
          <div className="btn-row">
            <button className="btn" onClick={async () => { await api.tick(); toast("Tick run"); refresh(); }}>Run a tick now</button>
            {running ? (
              <button className="btn" onClick={async () => { await api.pause(); toast("Paused: no new entries; risk checks keep running"); refresh(); }}>Pause</button>
            ) : (
              <button className="btn primary" onClick={async () => { await api.start(auto); toast("Bot running"); refresh(); }}>Start</button>
            )}
          </div>
        </div>
        <p className="caption" style={{ marginTop: 10 }}>
          Paused means no new entries. Margin top-ups, exits and the dead-man heartbeat keep protecting an open position.
        </p>
      </div>

      {s && (
        <div className="panel">
          <h2 className="section-title">Assets to scan</h2>
          <div className="btn-row">
            {ASSETS.map((a) => {
              const on = String(s.underlyings ?? "").split(",").includes(a.u);
              return (
                <label key={a.u} className="toggle" style={{ marginRight: 16 }}>
                  <input type="checkbox" checked={on} onChange={async () => {
                    const cur = String(s.underlyings ?? "").split(",").filter(Boolean);
                    const next = on ? cur.filter((x) => x !== a.u) : [...cur, a.u];
                    if (next.length === 0) { setErr("Keep at least one asset."); return; }
                    const order = ASSETS.map((x) => x.u).filter((u) => next.includes(u));
                    const saved = await api.saveSettings({ ...s, underlyings: order.join(",") });
                    setS(saved); await api.tick(); toast(`Scanning ${order.join(" + ")}`); refresh();
                  }} />
                  {a.label}
                </label>
              );
            })}
          </div>
          <p className="caption" style={{ marginTop: 10 }}>
            RWA only = tick just gold. Gold funding on Delta has been flat for 90 days, so the bot also prices short expiries there.
            On testnet the gold option books are nearly empty, so the fee guard will usually reject them; BTC is what trades on testnet.
          </p>
        </div>
      )}

      <div className="panel">
        <h2 className="section-title">Risk limits</h2>
        {!s && !err && <Loading />}
        {s && (
          <div className="form">
            {FIELDS.filter((f) => typeof s[f.key] === "number").map((f) => (
              <div key={f.key}>
                <label htmlFor={f.key}>{f.label}{f.asPct ? " (%)" : ""}</label>
                <input
                  id={f.key}
                  type="number"
                  value={draft[f.key] ?? ""}
                  min={f.min}
                  max={f.max}
                  step={f.step}
                  onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                />
                {f.hint && <div className="hint">{f.hint}</div>}
              </div>
            ))}
          </div>
        )}
        {err && <div className="banner" style={{ marginTop: 16, marginBottom: 0 }}><strong>Not saved.</strong> {err}</div>}
        {s && (
          <div className="btn-row" style={{ marginTop: 20, justifyContent: "flex-end" }}>
            <button className="btn primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save limits"}</button>
          </div>
        )}
      </div>

      <div className="panel attention">
        <div className="kicker" style={{ marginBottom: 10 }}>Rehearsal</div>
        <h2 className="section-title">Override the funding input</h2>
        <p className="support" style={{ marginTop: 0 }}>
          Testnet funding is pinned at 10.95%/yr, so the risk engine never sees a flip there. This replaces only the funding
          number the bot reads, so you can watch it hold, count down and exit. Orders are still real testnet orders. Every
          page shows a “Rehearsal” pill while this is on.
        </p>
        <div className="presets">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              className={`preset ${override === p.apr ? "on" : ""}`}
              title={p.note}
              onClick={async () => {
                await api.demoFunding(p.apr);
                await api.tick();
                toast(p.apr === null ? "Override cleared: real funding" : `Funding input set to ${p.apr}%/yr`);
                refresh();
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
