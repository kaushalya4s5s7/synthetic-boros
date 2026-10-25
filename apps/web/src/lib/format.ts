// One formatting vocabulary for every number on every page.

export function pct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

export function signedPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}%`;
}

/** USD with enough decimals that 1-contract testnet amounts are readable. */
export function usd(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  const digits = a >= 1000 ? 0 : a >= 1 ? 2 : 4;
  const body = a.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const sign = v < 0 ? "−" : signed ? "+" : "";
  return `${sign}$${body}`;
}

export function price(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `$${v.toLocaleString("en-US", { maximumFractionDigits: 1 })}`;
}

export function ago(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(now - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function date(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

export function dateTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("en-GB", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export const STATUS_LABEL: Record<string, string> = {
  FLAT: "No position",
  EARNING: "Earning",
  BELOW_FIXED: "Below fixed",
  FLIPPED: "Funding flipped",
  EXITING: "Exiting",
  HEDGE_BROKEN: "Hedge broken",
  EXPIRING: "Expiring",
};

/** States that need the user's eye get the blue inset (never red). */
export const ATTENTION = new Set(["FLIPPED", "EXITING", "HEDGE_BROKEN", "EXPIRING"]);
