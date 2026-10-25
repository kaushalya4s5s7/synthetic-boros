"""Environment contract for the bot loop."""
import os
from dataclasses import dataclass

from packages.exchange.keys import load_dotenv


@dataclass(frozen=True)
class Keys:
    tick_seconds: float
    demo_mode: bool


def load_keys() -> Keys:
    load_dotenv()
    return Keys(tick_seconds=float(os.getenv("TICK_SECONDS", "30") or 30),
                demo_mode=(os.getenv("DEMO_MODE", "0").strip().lower() in ("1", "true", "yes")))
