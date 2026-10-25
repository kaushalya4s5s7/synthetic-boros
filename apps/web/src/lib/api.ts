// The console's only path to the backend. Types mirror docs/repo-architecture.md §4.
import { API_URL } from "./config";

export type Level = "info" | "warn" | "action" | "critical";
export type StatusState = "FLAT" | "EARNING" | "BELOW_FIXED" | "FLIPPED" | "EXITING" | "HEDGE_BROKEN" | "EXPIRING";

export interface Health {
  ok: boolean;
  trading_env: string;
  funding_source: string;
  key_ok: boolean;
  error?: string | null;
  bot_mode: string;
  last_tick_ts: number | null;
}

export interface Asset {
  underlying: string;
  label: string;
  rwa: boolean;
  perp_symbol: string;
  spot: number;
  funding_now_apr: number;
  funding_avg24_apr: number;
  funding_stdev_apr: number | null;
  stable: boolean;
  pairs: number;
}

export interface Offer {
  id: string;
  underlying: string;
  label: string;
  rwa: boolean;
  expiry_ts: number;
  strike: number;
  days: number;
  fixed_apr: number;
  forecast_apr: number;
  contracts: number;
  notional_usd: number;
  capital_usd: number;
  gross_edge_usd: number;
  cost_usd: number;
  fees_usd: number;
  slippage_usd: number;
  net_edge_usd: number;
  net_edge_pct: number;
  fee_ratio: number;
  ok: boolean;
  reasons: string[];
  call_symbol?: string;
  put_symbol?: string;
  put_px?: number | null;
  call_px?: number | null;
  perp_px?: number | null;
  forward?: number | null;
  contract_value?: number;
}

export interface Leg {
  symbol: string;
  side: string;
  size: number;
  entry_price: number;
  mark: number | null;
  margin: number;
  liquidation_price: number | null;
  health_pct: number | null;
  distance_pct: number | null;
}

export interface Position {
  id: number;
  status: StatusState;
  status_reason: string;
  action: string;
  opened_ts: number;
  expiry_ts: number;
  days_left: number;
  strike: number;
  contracts: number;
  notional_usd: number;
  fixed_apr: number;
  floating_apr: number;
  spread_apr: number;
  funding_usd: number;
  fees_usd: number;
  earned_usd: number;
  projected_usd: number;
  worst_case_usd: number;
  reserve_usd: number;
  legs: Leg[];
}

export interface State {
  bot: { mode: "running" | "paused"; auto_enter: boolean; last_tick_ts: number | null; tick_seconds: number; stale: boolean };
  market: {
    spot: number | null;
    perp_mark: number | null;
    funding_now_apr: number | null;
    funding_avg24_apr: number | null;
    funding_source: string;
    override_apr: number | null;
    underlying?: string;
    assets?: Asset[];
  };
  account: { equity: number; available: number; margin_mode: string } | null;
  position: Position | null;
  decision: { action: string; reason: string; best: Offer | null } | null;
}

export interface OffersResp {
  decision: { action: string; reason: string } | null;
  offers: Offer[];
}

export interface FundingResp {
  source: string;
  points: { ts: number; apr: number }[];
  fixed_apr: number | null;
  exit_apr: number;
}

export interface EventRow {
  id: number;
  ts: number;
  level: Level;
  kind: string;
  message: string;
  data: unknown;
}

export interface PositionRow {
  id: number;
  state: "open" | "closed";
  opened_ts: number;
  closed_ts: number | null;
  expiry_ts: number;
  strike: number;
  contracts: number;
  fixed_apr: number;
  close_reason: string | null;
  realized_pnl_usd: number | null;
  funding_usd?: number;
  entry_fees_usd?: number;
}

export type Settings = Record<string, number | boolean | string>;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Bot API unreachable");
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const b = body as { detail?: unknown; message?: string } | null;
    const msg = typeof b?.detail === "string" ? b.detail : b?.message || `HTTP ${res.status}`;
    throw new ApiError(res.status, msg);
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) => call<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

export const api = {
  health: () => call<Health>("/health"),
  state: () => call<State>("/state"),
  offers: () => call<OffersResp>("/offers"),
  funding: (hours = 168) => call<FundingResp>(`/funding?hours=${hours}`),
  events: (limit = 100) => call<{ events: EventRow[] }>(`/events?limit=${limit}`),
  positions: () => call<{ positions: PositionRow[] }>("/positions"),
  settings: () => call<Settings>("/settings"),
  saveSettings: (s: Settings) => call<Settings>("/settings", { method: "PUT", body: JSON.stringify(s) }),
  start: (auto_enter: boolean) => post<{ ok: boolean }>("/bot/start", { auto_enter }),
  pause: () => post<{ ok: boolean }>("/bot/pause"),
  tick: () => post<{ ok: boolean }>("/bot/tick"),
  enter: (offer_id: string) => post<{ ok: boolean; message: string; position_id?: number }>("/enter", { offer_id }),
  close: () => post<{ ok: boolean; message: string }>("/position/close"),
  demoFunding: (apr: number | null) => post<{ ok: boolean }>("/demo/funding", { apr }),
};
