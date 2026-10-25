"""Wiring: which exchange, which ledger. DEMO_MODE=1 runs on the in-memory FakeExchange."""
from apps.bot.keys import load_keys as bot_keys
from apps.bot.runner import Bot
from packages.exchange import from_env
from packages.exchange.fake import FakeExchange
from packages.ledger import Ledger
from packages.ledger.keys import load_keys as ledger_keys


def build_bot() -> Bot:
    path = ledger_keys().path
    if bot_keys().demo_mode:
        fake = FakeExchange()
        ledger = Ledger(path.replace(".sqlite", "-demo.sqlite"))   # never mix demo and real positions
        return Bot(fake, fake, ledger, now=fake.now, demo=True)
    trade, funding = from_env()
    return Bot(trade, funding, Ledger(path))
