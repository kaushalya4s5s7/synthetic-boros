"""Environment contract for the ledger."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Keys:
    path: str


def load_keys() -> Keys:
    return Keys(path=os.environ.get("LEDGER_PATH", "./data/ledger.sqlite"))
