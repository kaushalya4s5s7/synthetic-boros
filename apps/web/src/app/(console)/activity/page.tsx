"use client";
import { api } from "@/lib/api";
import { usePolled } from "@/components/Live";
import { Empty, Loading, PageHead } from "@/components/ui";
import { date, dateTime, pct, price, usd } from "@/lib/format";

const LEVEL_LABEL = { info: "Info", warn: "Warning", action: "Action", critical: "Critical" } as const;

export default function Activity() {
  const { data: ev, err } = usePolled(() => api.events(200));
  const { data: pos } = usePolled(() => api.positions());

  return (
    <div className="stack">
      <PageHead
        title="Activity"
        support="Every decision the bot made and why: entries, funding payments, margin top-ups, alerts and exits. You hear about state changes, not every hourly wiggle in funding."
      />

      <div>
        <h2 className="section-title">Event log</h2>
        {err && !ev && <div className="banner"><strong>Couldn’t load events.</strong> {err}</div>}
        {!ev ? (
          !err && <Loading />
        ) : ev.events.length === 0 ? (
          <Empty title="No events yet">They appear after the bot’s first tick.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Time</th><th>Level</th><th>Event</th></tr></thead>
              <tbody>
                {ev.events.map((e) => (
                  <tr key={e.id} className={e.level === "critical" ? "attention" : ""}>
                    <td style={{ whiteSpace: "nowrap" }}>{dateTime(e.ts)}</td>
                    <td><span className={`chip ${e.level}`}>{LEVEL_LABEL[e.level] ?? e.level}</span></td>
                    <td>
                      {e.message}
                      <div className="caption">{e.kind}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div>
        <h2 className="section-title">Positions</h2>
        {!pos ? (
          <Loading />
        ) : pos.positions.length === 0 ? (
          <Empty title="No positions yet" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Opened</th><th>Expiry</th><th className="num">Strike</th><th className="num">Contracts</th>
                  <th className="num">Fixed</th><th className="num">Funding</th><th className="num">Final P&amp;L</th><th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {pos.positions.map((p) => (
                  <tr key={p.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{dateTime(p.opened_ts)}</td>
                    <td>{date(p.expiry_ts)}</td>
                    <td className="num">{price(p.strike)}</td>
                    <td className="num">{p.contracts}</td>
                    <td className="num">{pct(p.fixed_apr)}</td>
                    <td className="num">{usd(p.funding_usd ?? null, true)}</td>
                    <td className="num">{p.state === "open" ? <span className="chip ok">Open</span> : usd(p.realized_pnl_usd, true)}</td>
                    <td className="reasons">{p.close_reason ?? (p.state === "open" ? "Running" : "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
