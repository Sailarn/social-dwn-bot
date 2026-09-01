"""Entry point.

Deliberately tiny and left at the repo root: ecosystem.config.js, the systemd
unit and the Dockerfile all run `python bot.py`, so the code can be reorganised
underneath without touching any deployment file.
"""

from src.app import run

if __name__ == "__main__":
    run()
