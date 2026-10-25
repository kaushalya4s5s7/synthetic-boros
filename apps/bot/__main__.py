"""Headless loop: python -m apps.bot"""
import time

from apps.api.deps import build_bot
from apps.bot.keys import load_keys


def main() -> None:
    keys = load_keys()
    bot = build_bot()
    print(f"bot running: trading={bot.trade.env} funding={bot.funding_src.env} tick={keys.tick_seconds}s")
    while True:
        bot.tick()
        s = bot.status
        print(time.strftime("%H:%M:%S"), s.state if s else "-", "|", (s.reason if s else bot.last_error) or "",
              "|", bot.decision.reason if bot.decision else "")
        time.sleep(keys.tick_seconds)


if __name__ == "__main__":
    main()
