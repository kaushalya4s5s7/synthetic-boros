"""Environment contract for packages/exchange. The only place exchange code reads the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Load KEY=VALUE lines from .env without overriding variables already set. Inline '#' comments are dropped."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


@dataclass(frozen=True)
class Keys:
    key: str | None
    secret: str | None
    trading_env: str
    funding_source: str


def load_keys() -> Keys:
    load_dotenv()
    trading = os.getenv("TRADING_ENV", "testnet").strip() or "testnet"
    funding = os.getenv("FUNDING_SOURCE", "mainnet").strip() or "mainnet"
    for name, v in (("TRADING_ENV", trading), ("FUNDING_SOURCE", funding)):
        if v not in ("testnet", "mainnet"):
            raise ValueError(f"{name} must be 'testnet' or 'mainnet', got {v!r}")
    return Keys(os.getenv("DELTA_KEY") or None, os.getenv("DELTA_SECRET") or None, trading, funding)
