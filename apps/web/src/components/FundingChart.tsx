"use client";
// Inline SVG: floating funding (blue), your locked fixed rate (dashed ink), exit line (hairline at exit_apr).
import type { FundingResp } from "@/lib/api";

const W = 720, H = 220, PAD_L = 44, PAD_R = 24, PAD_T = 12, PAD_B = 26;

export function FundingChart({ data }: { data: FundingResp }) {
  const pts = data.points;
  if (pts.length < 2) {
    return <div className="field-dots empty"><div className="card">Not enough funding history yet.</div></div>;
  }
  const vals = pts.map((p) => p.apr);
  const refs = [data.exit_apr, ...(data.fixed_apr !== null ? [data.fixed_apr] : [])];
  let lo = Math.min(...vals, ...refs);
  let hi = Math.max(...vals, ...refs);
  const pad = Math.max(1, (hi - lo) * 0.12);
  lo -= pad;
  hi += pad;
  const t0 = pts[0].ts, t1 = pts[pts.length - 1].ts;
  const x = (t: number) => PAD_L + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD_L - PAD_R);
  const y = (v: number) => PAD_T + (1 - (v - lo) / (hi - lo)) * (H - PAD_T - PAD_B);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(p.ts).toFixed(1)},${y(p.apr).toFixed(1)}`).join(" ");

  const ticks = 4;
  const yTicks = Array.from({ length: ticks + 1 }, (_, i) => lo + ((hi - lo) * i) / ticks);
  const days = Math.max(1, Math.round((t1 - t0) / 86400));
  const xTicks = Array.from({ length: Math.min(days, 7) + 1 }, (_, i) => t0 + ((t1 - t0) * i) / Math.min(days, 7));

  return (
    <div>
      <div className="field-dots" style={{ padding: 12 }}>
        <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Funding rate versus your fixed rate">
          <rect x={PAD_L} y={PAD_T} width={W - PAD_L - PAD_R} height={H - PAD_T - PAD_B} fill="#fff" rx="6" />
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={PAD_L} x2={W - PAD_R} y1={y(v)} y2={y(v)} stroke="#ededed" strokeWidth="1" />
              <text x={PAD_L - 6} y={y(v) + 4} textAnchor="end" fontSize="11" fill="#7f7f7f">{v.toFixed(0)}%</text>
            </g>
          ))}
          {xTicks.map((t) => (
            <text key={t} x={x(t)} y={H - 8} textAnchor="middle" fontSize="11" fill="#7f7f7f">
              {new Date(t * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}
            </text>
          ))}
          <line x1={PAD_L} x2={W - PAD_R} y1={y(data.exit_apr)} y2={y(data.exit_apr)} stroke="#7f7f7f" strokeWidth="1" />
          <text x={W - PAD_R - 4} y={y(data.exit_apr) - 5} textAnchor="end" fontSize="11" fill="#7f7f7f">exit line (24h avg)</text>
          {data.fixed_apr !== null && (
            <>
              <line x1={PAD_L} x2={W - PAD_R} y1={y(data.fixed_apr)} y2={y(data.fixed_apr)} stroke="#1e1e1e" strokeWidth="1.5" strokeDasharray="5 4" />
              <text x={W - PAD_R - 4} y={y(data.fixed_apr) - 5} textAnchor="end" fontSize="11" fill="#1e1e1e">your fixed {data.fixed_apr.toFixed(2)}%</text>
            </>
          )}
          <path d={line} fill="none" stroke="#0077ff" strokeWidth="2" strokeLinejoin="round" />
        </svg>
      </div>
      <div className="legend">
        <span><i />Floating funding ({data.source})</span>
        {data.fixed_apr !== null && <span><i className="dash" />Your locked fixed rate</span>}
        <span><i className="exit" />Exit line: bot exits if the 24h average stays below {data.exit_apr}%</span>
      </div>
    </div>
  );
}
