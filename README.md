# sectornews

Terminal sector intelligence dashboard — like pftui but focused on news sentiment and ETF recommendations.

**No API keys. No cost. Pure Python stdlib.**

## What it does

- Pulls free public RSS feeds from Reuters, AP, MarketWatch, CNBC, FT
- Scores each headline for **sentiment** (bullish/bearish/neutral)
- **Classifies by sector** (Energy, Tech, Defense, Financials, Commodities, Healthcare, Consumer, Real Estate)
- Computes **sector sentiment scores** from the news signal mix
- Recommends **ETFs** ranked by conviction score based on sector signals
- Auto-refreshes every 5 minutes

## Install (macOS)

```bash
cd sectornews
chmod +x install.sh
./install.sh
```

Then just run:

```bash
sectornews
```

## Or run directly

```bash
python3 sectornews.py
```

## Controls

| Key | Action |
|-----|--------|
| `1` | News feed view |
| `2` | Sector sentiment scores |
| `3` | ETF watchlist |
| `4` | Sentiment charts |
| `↑↓` | Navigate news list |
| `R` | Refresh feeds now |
| `Q` | Quit |

## News Sources (all free public RSS)

- Reuters Business & Tech
- Associated Press Business & Tech
- MarketWatch Real-time Headlines
- CNBC Markets & Business
- Financial Times
- Investopedia

## How Sector Scoring Works

Each headline is scored 0–100 based on the ratio of positive to negative signal words found in the text. Headlines are then classified into sectors using keyword matching. The sector score is the rolling average sentiment of all headlines in that sector over the past 48 hours.

## ETF Recommendations

ETFs are ranked by conviction (1.0–5.0 stars) derived from their sector's sentiment score. High conviction = sector news is predominantly bullish. Low conviction = predominantly bearish signals.

## Requirements

- Python 3.8+
- macOS / Linux
- Internet connection for live feeds
- No pip installs needed
