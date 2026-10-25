// The only place the console reads its environment.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
export const POLL_MS = 5000;
