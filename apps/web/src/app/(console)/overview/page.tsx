"use client";
import Link from "next/link";
import { useLive } from "@/components/Live";
import { Loading, Stat } from "@/components/ui";
import { ATTENTION, STATUS_LABEL, pct, signedPct, usd } from "@/lib/format";

function ConnectPanel({ error, ipBlocked }: { error?: string | null; ipBlocked: boolean }) {
  return (
    <div className="panel attention">
      <div className="kicker" style={{ marginBottom: 12 }}>Connect your Delta account</div>
      <h2 className="section-title">{ipBlocked ? "Delta rejected this machine’s IP address" : "The bot has no working API key"}</h2>
      <p className="support" style={{ marginTop: 0 }}>
        The key never enters this browser. It lives in the server’s <code>.env</code> file and only the bot uses it.
      </p>
      <ol className="numbered">
        <li><b>01</b>Open testnet.delta.exchange → Account → API Keys and create a key with <em>Trading</em> permission.</li>
        <li><b>02</b>Whitelist this machine’s public IP on the key{ipBlocked && error ? ` (Delta saw: ${error})` : ""}. Changing Wi-Fi changes the IP.</li>
        <li><b>03</b>Put <code>DELTA_KEY</code> and <code>DELTA_SECRET</code> in <code>.env</code> at the repo root and restart the API.</li>
      </ol>
      {error && <p className="caption">Last error: {error}</p>}
    </div>
  );
}

export default function Overview() {
  const { state, health, error, loading } = useLive();
  if (loading) return <Loading />;
  if (!health) return null; // the shell shows the "unreachable" banner

  const blocked = !health.key_ok || (health.error || "").includes("ip_not_whitelisted");
  const m = state?.market;
  const pos = state?.position;
  const best = state?.decision?.best ?? null;
  const status = pos?.status ?? "FLAT";

  return (
    <div className="stack">
      <div>
        <span className="eyebrow">Fixed vs floating, on Delta</span>
        <h1 className="statement" style={{ marginTop: 20, maxWidth: 920 }}>
          Lock a fixed rate with options, earn Delta’s floating funding,{" "}
          <span className="faint">hedged against the asset’s price.</span>
        </h1>
        <p className="support">
          The bot buys a fixed rate cheaply (a call bought and a put sold at the same strike), shorts the perp to collect
          funding every 8 hours, and only trades when the expected funding edge beats every fee and spread at least two to one.
        </p>
      </div>

      {blocked && <ConnectPanel error={health.error || error} ipBlocked={(health.error || "").includes("ip_not_whitelisted")} />}

      <div className="field-dots">
        <div className="stats four">
          <Stat fig={pct(m?.funding_now_apr)} label={`${m?.underlying ?? ""} funding now (yearly)`} src={`from ${m?.funding_source ?? health.funding_source}`} blue />
          <Stat fig={pct(m?.funding_avg24_apr)} label="24h average, used for decisions" />
          <Stat fig={best ? pct(best.fixed_apr) : "—"} label={best ? `Best fixed rate · ${best.days.toFixed(0)} days` : "No fixed rate passes the filters"} />
          <Stat
            fig={STATUS_LABEL[status] ?? status}
            label={pos ? `${signedPct(pos.spread_apr)} / yr spread · ${usd(pos.earned_usd, true)} so far` : "No open position"}
            blue={ATTENTION.has(status)}
          />
        </div>
      </div>

      {m?.assets && m.assets.length > 0 && (
        <div>
          <h2 className="section-title">Assets scanned</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Asset</th><th className="num">Funding now</th><th className="num">7-day spread</th><th>Funding</th><th className="num">Option pairs</th></tr>
              </thead>
              <tbody>
                {m.assets.map((a) => (
                  <tr key={a.underlying}>
                    <td><b style={{ fontWeight: 500 }}>{a.underlying}</b><div className="caption">{a.label}</div></td>
                    <td className="num">{pct(a.funding_now_apr)}</td>
                    <td className="num">{a.funding_stdev_apr === null ? "—" : `±${a.funding_stdev_apr.toFixed(2)}`}</td>
                    <td>{a.stable ? <span className="chip ok">Flat: short expiries allowed</span> : <span className="muted">Moves: 21+ day expiries only</span>}</td>
                    <td className="num">{a.pairs}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="caption" style={{ marginTop: 8 }}>
            Funding read from {m.funding_source}. Tokenised gold (XAUT) funding sat at one value for the last 90 days, so a flip is not a risk there; the fee guard still decides.
          </p>
        </div>
      )}

      {pos ? (
        <div className={`panel ${ATTENTION.has(status) ? "attention" : ""}`}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
            <div>
              <div className="section-title" style={{ marginBottom: 4 }}>{STATUS_LABEL[status]}: {pos.status_reason}</div>
              <div className="muted">
                You locked {pct(pos.fixed_apr)} · funding {pct(pos.floating_apr)} · {pos.days_left.toFixed(1)} days left
              </div>
            </div>
            <Link className="btn primary lg" href="/position">View position</Link>
          </div>
        </div>
      ) : (
        <div className="panel">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
            <div>
              <div className="section-title" style={{ marginBottom: 4 }}>
                {best && state?.decision?.action === "enter"
                  ? `Today’s offer: lock ${pct(best.fixed_apr)} for ${best.days.toFixed(0)} days`
                  : "No trade right now"}
              </div>
              <div className="muted">{state?.decision?.reason ?? "Waiting for the first market scan."}</div>
            </div>
            <Link className="btn primary lg" href="/offers">Review today’s offer</Link>
          </div>
        </div>
      )}
    </div>
  );
}
