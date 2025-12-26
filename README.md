# Bonbast Telegram Bot (Lightweight)

Scrapes https://bonbast.com/ (fast: extracts /json param from homepage, then POSTs /json).

## Features
- Per-channel config (currencies/coins/gold/btc)
- Interval aligned to wall-clock (12:00, 12:05, ...)
- Quiet hours (Tehran time)
- Only-on-change + triggers + thresholds
- Manual Send Now
- Owner approval (green-light) for any chat

## Run (manual)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="..."
export OWNER_IDS="12345678,87654321"
python main.py
