# Seoul Subway Telegram Bot 🚇

A Telegram chatbot that provides real-time Seoul Metro arrival information. Query upcoming trains at any station, filter by route direction, and save commute presets for quick daily checks.

## Prerequisites

1. **Telegram Bot Token** — Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. **Seoul Open Data API Key** — Register at [data.seoul.go.kr](https://data.seoul.go.kr) and apply for the **실시간 지하철** (real-time subway) API key

## Setup

```bash
# Clone and install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env with your Telegram bot token and Seoul API key

# Run the bot
python bot.py
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and usage guide |
| `/arrivals <역이름>` | Real-time arrivals at a station (e.g. `/arrivals 강남`) |
| `/route <출발역> <도착역>` | Next 3 trains from departure toward arrival (e.g. `/route 강남 잠실`) |
| `/addpreset <이름> <출발역> <도착역>` | Save a named commute preset |
| `/presets` | List your saved presets |
| `/go <이름>` | Run a saved preset |
| `/delpreset <이름>` | Delete a saved preset |
| `/morning` | Shortcut for `/go morning` |
| `/evening` | Shortcut for `/go evening` |

## Example Usage

```
# Check all arrivals at Gangnam station
/arrivals 강남

# Find next trains from Gangnam toward Jamsil
/route 강남 잠실

# Save your morning commute
/addpreset morning 강남 잠실

# Quick check every morning
/morning
```

## Supported Lines

1-9호선, 경의중앙선, 공항철도, 경춘선, 수인분당선, 신분당선, 우이신설선

## Project Structure

```
├── bot.py           # Telegram bot handlers and main entry point
├── subway_api.py    # Seoul Metro real-time arrival API client
├── station_data.py  # Station database with line/direction mapping
├── presets.py       # Per-user preset storage (JSON files)
├── requirements.txt
├── .env.example
└── README.md
```

## Notes

- The real-time API provides arrival data for trains currently approaching a station. During off-hours, fewer or no results may be returned.
- Route queries (`/route`) work for stations on the same direct line. For routes requiring transfers, use `/arrivals` for each leg separately.
- Station names must be in Korean (한글) as used by the Seoul Metro system.
