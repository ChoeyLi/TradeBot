#!/usr/bin/env python3
"""
sectornews - terminal sector intelligence dashboard
Pulls free public RSS news feeds, scores sector sentiment,
and recommends ETFs based on signals.

No API keys. No cost. Pure stdlib.
"""

import curses
import threading
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import json
import re
import os
import sys
import ssl
import math
from datetime import datetime, timezone
from collections import defaultdict

# Chrome User-Agent — same trick pftui uses to bypass feed restrictions
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# SSL context (avoids macOS cert-store issues)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ─── RSS FEEDS (free & public) ───────────────────────────────────────────────

RSS_FEEDS = [
    # Bloomberg (free public RSS)
    ("Bloomberg",    "https://feeds.bloomberg.com/markets/news.rss"),
    ("Bloomberg",    "https://feeds.bloomberg.com/economics/news.rss"),
    ("Bloomberg",    "https://feeds.bloomberg.com/politics/news.rss"),
    ("Bloomberg",    "https://feeds.bloomberg.com/commodities/news.rss"),
    # Reuters
    ("Reuters",      "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters",      "https://feeds.reuters.com/reuters/technologyNews"),
    # AP
    ("AP",           "https://feeds.apnews.com/rss/apf-business"),
    ("AP",           "https://feeds.apnews.com/rss/apf-technology"),
    # CNBC
    ("CNBC",         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("CNBC",         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147"),
    # MarketWatch
    ("MarketWatch",  "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
    # Yahoo Finance
    ("Yahoo",        "https://finance.yahoo.com/news/rssindex"),
    # Investopedia
    ("Investopedia", "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_headline"),
    # Seeking Alpha
    ("Seeking Alpha","https://seekingalpha.com/feed.xml"),
]

# ─── SECTOR CLASSIFICATION ───────────────────────────────────────────────────

SECTOR_KEYWORDS = {
    "Energy":      ["oil","gas","lng","opec","brent","wti","crude","pipeline","refinery",
                    "petroleum","fuel","energy","shell","bp","exxon","chevron","halliburton",
                    "hormuz","iran","russia","saudi","natural gas","coal"],
    "Technology":  ["ai","artificial intelligence","semiconductor","chip","nvidia","apple",
                    "google","microsoft","meta","amazon","software","cloud","data center",
                    "quantum","cyber","antitrust","tech","silicon","startup","algorithm",
                    "machine learning","openai","anthropic","robot"],
    "Defense":     ["defense","military","nato","weapon","missile","drone","army","navy",
                    "air force","pentagon","lockheed","raytheon","boeing","northrop",
                    "spending","war","conflict","geopolit","sanction","ceasefire","treaty"],
    "Financials":  ["bank","fed","federal reserve","interest rate","inflation","bond",
                    "treasury","rate cut","rate hike","credit","mortgage","lending",
                    "jpmorgan","goldman","citigroup","wells fargo","boe","ecb","imf",
                    "liquidity","capital","debt","yield"],
    "Commodities": ["gold","silver","copper","iron","steel","aluminum","lithium","zinc",
                    "nickel","commodity","metal","mining","corn","wheat","soy","coffee",
                    "cbot","comex","futures","supply chain","inventory","brics","reserve"],
    "Healthcare":  ["fda","drug","pharma","biotech","vaccine","clinical trial","approval",
                    "pfizer","moderna","merck","johnson","hospital","healthcare","medicare",
                    "medicaid","glp-1","obesity","cancer","genomic","biotech"],
    "Consumer":    ["retail","consumer","spending","tariff","walmart","amazon","sales",
                    "confidence","sentiment","housing","inflation","price","cost","luxury",
                    "discretionary","staples","ecommerce","brand","supply"],
    "Real Estate": ["real estate","reit","housing","mortgage","property","rent","commercial",
                    "office","residential","home sales","construction","homebuilder",
                    "interest rate","affordability","30-year"],
}

# ─── THEME CLASSIFICATION ────────────────────────────────────────────────────
# High-level category visible in the News tab (like pftui's "Category" column)

THEME_KEYWORDS = {
    "geopolitics": ["war","military","nato","weapon","missile","drone","sanction",
                    "ceasefire","treaty","diplomacy","invasion","strike","nuclear",
                    "geopolit","alliance","tariff","trade war","taiwan","coup",
                    "iran","ukraine","russia","north korea","middle east","pentagon",
                    "arms","rebel","blockade","protest","unrest","tensions"],
    "macro":       ["fed","federal reserve","central bank","rate cut","rate hike",
                    "rate pause","inflation","cpi","pce","gdp","recession","stagflation",
                    "employment","unemployment","jobs","fiscal","deficit","imf",
                    "world bank","ecb","boe","boj","monetary policy","stimulus",
                    "quantitative","yield curve","debt ceiling","treasury yield"],
    "crypto":      ["bitcoin","btc","ethereum","eth","crypto","blockchain","defi",
                    "nft","stablecoin","altcoin","token","mining","solana","xrp",
                    "usdc","usdt","binance","coinbase","web3","dao"],
    "markets":     ["stock","equity","s&p","nasdaq","dow","ipo","merger","acquisition",
                    "earnings","revenue","dividend","buyback","index","etf","fund",
                    "portfolio","analyst","upgrade","downgrade","price target","shares",
                    "rally","selloff","volatility","options","futures"],
}

def classify_theme(text):
    t = text.lower()
    best, best_n = "markets", 0   # default
    for theme, kws in THEME_KEYWORDS.items():
        n = sum(1 for kw in kws if kw in t)
        if n > best_n:
            best, best_n = theme, n
    return best

# ─── SENTIMENT KEYWORDS ───────────────────────────────────────────────────────
# Phrase-based scoring: multi-word patterns score ×2 so context beats single words
# "cut" removed from neg single-words — "rate cut" is a phrase that scores bullish

BULLISH_PHRASES = [
    "rate cut","rate cuts","cuts rate","cut rates","lower rates","rate reduction",
    "ceasefire","peace deal","peace talks","trade deal","deal reached","sanctions lifted",
    "record high","all-time high","beats expectations","beat expectations",
    "earnings beat","better than expected","profit rises","profit surges",
    "dovish","stimulus","quantitative easing","debt deal","budget deal",
    "job creation","jobs added","unemployment falls","unemployment drops",
    "approved","approval granted","fda approved","merger approved",
    "rebound","recovery","upgrade","breakout","strategic reserve",
]
BEARISH_PHRASES = [
    "rate hike","rate hikes","hikes rate","raises rates","rate increase",
    "hawkish","quantitative tightening","emergency rate",
    "earnings miss","misses expectations","miss expectations","worse than expected",
    "profit warning","guidance cut","revenue miss","write-down",
    "bank failure","bank run","credit downgrade","debt default","sovereign default",
    "recession fears","recession risk","recession warning","depression",
    "war escalates","escalation","invades","invasion","military strike",
    "job losses","mass layoffs","unemployment rises","unemployment surges",
    "sanctions imposed","sanctions expanded","trade war escalates",
    "debt crisis","financial crisis","market crash","flash crash",
]
POSITIVE_WORDS = [
    "surge","soar","rise","gain","rally","jump","climb","boost","record","high",
    "growth","profit","beat","strong","bullish","upside","expand","recovery",
    "approve","buy","upgrade","positive","optimism","acceleration","demand",
    "breakthrough","secure","win","deal","partnership","increase","thrive",
    "rebound","stabilize","confidence","resolution","agreement","eases",
]
NEGATIVE_WORDS = [
    "fall","drop","slump","crash","plunge","decline","loss","miss","weak",
    "bearish","risk","warn","collapse","fail","downgrade","uncertainty",
    "concern","threat","tension","conflict","tariff","sanction","ban","restrict",
    "recession","contraction","default","crisis","investigation","probe",
    "selloff","panic","fear","instability","tumble","sink","slide",
]

# ─── ETF RECOMMENDATIONS ─────────────────────────────────────────────────────

SECTOR_ETFS = {
    "Energy":      [("XLE","Energy Select Sector SPDR"),("VDE","Vanguard Energy ETF"),
                    ("USO","US Oil Fund"),("AMLP","Alerian MLP ETF"),("FCG","Natural Gas ETF")],
    "Technology":  [("QQQ","Invesco Nasdaq-100 ETF"),("XLK","Technology Select SPDR"),
                    ("SOXX","iShares Semiconductor ETF"),("SMH","VanEck Semiconductor"),
                    ("ARKK","ARK Innovation ETF")],
    "Defense":     [("ITA","iShares US Aerospace & Defense"),("XAR","SPDR S&P Aerospace"),
                    ("PPA","Invesco Aerospace & Defense"),("DFEN","Direxion Daily Aerospace")],
    "Financials":  [("XLF","Financials Select Sector SPDR"),("KBE","SPDR S&P Bank ETF"),
                    ("KRE","SPDR S&P Regional Bank"),("IXG","iShares Global Financials")],
    "Commodities": [("GLD","SPDR Gold Shares"),("IAU","iShares Gold Trust"),
                    ("SLV","iShares Silver Trust"),("PDBC","Invesco Commodity ETF"),
                    ("GDXJ","VanEck Junior Gold Miners")],
    "Healthcare":  [("XLV","Health Care Select Sector"),("IBB","iShares Biotech ETF"),
                    ("VHT","Vanguard Health Care ETF"),("ARKG","ARK Genomic Revolution")],
    "Consumer":    [("XLY","Consumer Discret. Select SPDR"),("XLP","Consumer Staples SPDR"),
                    ("XRT","SPDR S&P Retail ETF"),("IEDI","iShares Evolved US Discret.")],
    "Real Estate": [("VNQ","Vanguard Real Estate ETF"),("IYR","iShares US Real Estate"),
                    ("XLRE","Real Estate Select SPDR"),("REM","iShares Mortgage Real Est.")],
}

# ─── MARKET DATA SYMBOLS ─────────────────────────────────────────────────────
# (display, full name, yahoo_ticker, category)
MKT_SYMBOLS = [
    ("SPX",    "S&P 500",              "^GSPC",    "equity"),
    ("NDX",    "Nasdaq 100",           "^NDX",     "equity"),
    ("DJI",    "Dow Jones",            "^DJI",     "equity"),
    ("RUT",    "Russell 2000",         "^RUT",     "equity"),
    ("VIX",    "CBOE Volatility",      "^VIX",     "equity"),
    ("Gold",   "Gold Futures",         "GC=F",     "commodity"),
    ("Silver", "Silver Futures",       "SI=F",     "commodity"),
    ("Oil",    "Crude Oil (WTI)",      "CL=F",     "commodity"),
    ("NatGas", "Natural Gas",          "NG=F",     "commodity"),
    ("BTC",    "Bitcoin",              "BTC-USD",  "crypto"),
    ("ETH",    "Ethereum",             "ETH-USD",  "crypto"),
    ("SOL",    "Solana",               "SOL-USD",  "crypto"),
    ("DXY",    "Dollar Index",         "DX-Y.NYB", "forex"),
    ("EUR",    "Euro / USD",           "EURUSD=X", "forex"),
    ("GBP",    "Pound / USD",          "GBPUSD=X", "forex"),
    ("JPY",    "USD / Yen",            "JPY=X",    "forex"),
    ("10Y",    "10-Year Treasury",     "^TNX",     "fund"),
    ("2Y",     "2-Year Treasury",      "^FVX",     "fund"),
    ("HYG",    "High Yield Bond ETF",  "HYG",      "fund"),
    ("LQD",    "Inv Grade Bond ETF",   "LQD",      "fund"),
]

# Persistent history file for sentiment snapshots
HIST_FILE = os.path.expanduser("~/.sectornews_history.json")

# ─── COLOR PAIRS ─────────────────────────────────────────────────────────────

C_HEADER   = 1   # cyan  — top bar brand
C_POS      = 2   # green — bullish
C_NEG      = 3   # red   — bearish
C_NEU      = 4   # yellow— neutral
C_SEL      = 5   # black on cyan — selected row
C_DIM      = 6   # dark grey — secondary text
C_TITLE    = 7   # bright white — main text
C_ACCENT   = 8   # cyan — source names, tickers
C_BAR_POS  = 9
C_BAR_NEG  = 10
C_BAR_NEU  = 11
C_BORDER   = 12  # dark grey border
C_LOADING  = 13  # yellow loading indicator
C_TRUMP    = 14  # magenta — Trump headlines
C_GEOPOL   = 15  # red — geopolitics theme
C_MACRO    = 16  # yellow — macro theme
C_CRYPTO   = 17  # cyan — crypto theme
C_MKTS     = 18  # white — markets theme

# ─── APP STATE ────────────────────────────────────────────────────────────────

class AppState:
    def __init__(self):
        self.news           = []
        self.loading        = True
        self.error          = None
        self.tab            = 0          # 0=News 1=Sectors 2=Mkt 3=Watchlist 4=Chart
        self.sel            = 0
        self.scroll         = 0
        self.etf_scroll     = 0
        self.last_update    = None
        self.feeds_ok       = 0
        self.feeds_total    = len(RSS_FEEDS)
        self.sector_filter  = None       # set when user clicks a sector name
        self.mkt_data        = []   # list of market quote dicts
        self.mkt_loading     = False
        self.mkt_last_update = None
        self.polymarket      = []   # finance-relevant prediction markets
        self.history         = []   # last 3 sentiment snapshots (30-min cadence)
        self.lock           = threading.Lock()

# ─── RSS FETCH ────────────────────────────────────────────────────────────────

def fetch_feed(source, url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent":      _UA,
            "Accept":          "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control":   "no-cache",
        })
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            data = r.read()
        root  = ET.fromstring(data)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        articles = []
        for item in items[:8]:
            title = (item.findtext("title") or
                     item.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            title = re.sub(r"<[^>]+>", "", title)
            pub   = (item.findtext("pubDate") or
                     item.findtext("{http://www.w3.org/2005/Atom}published") or "")
            if title:
                articles.append({"source": source, "title": title, "pub": pub})
        return articles, True
    except Exception:
        return [], False

def score_sentiment(text):
    t = text.lower()
    # Phrases count double to beat ambiguous single words (e.g. "rate cut" > "cut")
    pos = sum(2 for ph in BULLISH_PHRASES if ph in t)
    neg = sum(2 for ph in BEARISH_PHRASES if ph in t)
    pos += sum(1 for w in POSITIVE_WORDS if w in t)
    neg += sum(1 for w in NEGATIVE_WORDS if w in t)
    total = pos + neg
    if total == 0:
        return 50
    return max(0, min(100, int((pos / total) * 100)))

def classify_sector(text):
    t = text.lower()
    scores = {}
    for sector, kws in SECTOR_KEYWORDS.items():
        scores[sector] = sum(1 for kw in kws if kw in t)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Macro"

def enrich(article):
    text = article["title"]
    article["sentiment"] = score_sentiment(text)
    article["sector"]    = classify_sector(text)
    article["theme"]     = classify_theme(text)
    article["age"]       = format_age(article.get("pub",""))
    article["signal"]    = ("bullish" if article["sentiment"] >= 60
                            else "bearish" if article["sentiment"] <= 40
                            else "neutral")
    return article

def format_age(pub_str):
    if not pub_str:
        return "?"
    fmts = ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"]
    for fmt in fmts:
        try:
            dt = datetime.strptime(pub_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            diff = int((datetime.now(timezone.utc) - dt).total_seconds())
            if diff < 60:   return f"{diff}s"
            if diff < 3600: return f"{diff//60}m"
            if diff < 86400:return f"{diff//3600}h"
            return f"{diff//86400}d"
        except Exception:
            pass
    return "?"

def _age_secs(age_str):
    """Parse '2m' / '3h' / '1d' into seconds."""
    try:
        if age_str.endswith('s'): return int(age_str[:-1])
        if age_str.endswith('m'): return int(age_str[:-1]) * 60
        if age_str.endswith('h'): return int(age_str[:-1]) * 3600
        if age_str.endswith('d'): return int(age_str[:-1]) * 86400
    except Exception:
        pass
    return 86400

def compute_sector_scores(news):
    """Recency-weighted sentiment average.
    Weights: <2 h = 3, 2–12 h = 2, older = 1.
    Requires ≥2 articles to move off 50; single-article sectors return raw score.
    """
    sector_data = defaultdict(list)
    for a in news:
        secs = _age_secs(a.get("age", "1d"))
        w = 3 if secs <= 7200 else (2 if secs <= 43200 else 1)
        sector_data[a["sector"]].append((a["sentiment"], w))
    scores = {}
    for sector in SECTOR_KEYWORDS:
        items = sector_data.get(sector, [])
        if not items:
            scores[sector] = 50
        elif len(items) == 1:
            scores[sector] = items[0][0]
        else:
            total_w = sum(w for _, w in items)
            scores[sector] = int(sum(s * w for s, w in items) / total_w)
    return dict(sorted(scores.items(), key=lambda x: -x[1]))

def compute_etf_recommendations(sector_scores):
    recs = []
    for sector, score in sector_scores.items():
        etfs = SECTOR_ETFS.get(sector, [])
        for ticker, name in etfs[:2]:
            conviction = round((score / 100) * 5, 1)
            recs.append({
                "ticker": ticker, "name": name,
                "sector": sector, "score": conviction,
                "sentiment": score,
            })
    return sorted(recs, key=lambda x: -x["score"])

def fetch_all_news(state):
    results = [[] for _ in RSS_FEEDS]
    ok_flags = [False] * len(RSS_FEEDS)

    def fetch_one(i, source, url):
        articles, ok = fetch_feed(source, url)
        results[i] = [enrich(a) for a in articles]
        ok_flags[i] = ok

    threads = [threading.Thread(target=fetch_one, args=(i, src, url), daemon=True)
               for i, (src, url) in enumerate(RSS_FEEDS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    all_articles = [a for batch in results for a in batch]
    all_articles.sort(key=lambda x: _age_secs(x.get("age", "?")))
    with state.lock:
        state.news        = all_articles[:60]
        state.loading     = False
        state.feeds_ok    = sum(ok_flags)
        state.last_update = datetime.now().strftime("%H:%M:%S")
        state.error       = None if all_articles else "No articles loaded — check your internet connection"

# ─── MARKET DATA FETCH ───────────────────────────────────────────────────────
# Uses Yahoo Finance /v8/finance/chart per-symbol (more reliable than batch quote)

import http.cookiejar as _cookiejar
import subprocess
import urllib.parse as _urlparse

def _fetch_chart(ticker, retries=2):
    """Fetch 5-day daily OHLC via curl — avoids Python urllib 429 fingerprinting."""
    sym_enc = _urlparse.quote(ticker)
    url = (f"https://query1.finance.yahoo.com/v7/finance/chart/{sym_enc}"
           "?range=5d&interval=1d")
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ["curl", "-s", "-m", "10", "-A", "Mozilla/5.0", url],
                capture_output=True, timeout=12,
            )
            d      = json.loads(r.stdout)
            res    = d["chart"]["result"][0]
            meta   = res["meta"]
            closes = [c for c in res["indicators"]["quote"][0].get("close", []) if c]
            price  = meta.get("regularMarketPrice")
            prev   = meta.get("chartPreviousClose") or (closes[-2] if len(closes) >= 2 else None)
            chg    = round(((price - prev) / prev * 100), 2) if price and prev else None
            price  = round(price, 2) if price else None
            return price, chg, closes
        except Exception:
            if attempt < retries - 1:
                time.sleep(1.0)
    return None, None, []

def fetch_mkt_data(state):
    """Fetch live quotes sequentially with gaps to stay under Yahoo rate limits."""
    with state.lock:
        state.mkt_loading = True

    rows = []
    for display, name, ticker, cat in MKT_SYMBOLS:
        price, chg, closes = _fetch_chart(ticker)
        rows.append({
            "symbol":   display, "name": name,
            "ticker":   ticker,  "category": cat,
            "price":    price,   "chg_pct": chg,
            "closes":   closes,
        })
        time.sleep(0.4)   # 400 ms gap to avoid 429

    # Fill any None slots (thread timeout)
    with state.lock:
        state.mkt_data        = rows
        state.mkt_loading     = False
        state.mkt_last_update = datetime.now().strftime("%H:%M:%S")

# ─── POLYMARKET PREDICTION MARKETS ──────────────────────────────────────────

_POLY_KWS = [
    "fed","rate cut","rate hike","interest rate","recession","gdp","inflation","cpi",
    "tariff","dollar","gold","oil","nasdaq","s&p","crypto","bitcoin","btc","eth",
    "ethereum","economy","debt","treasury","iran","ukraine","war","ceasefire","trump",
    "china","trade","powell","fiscal","deficit","opec","brent","wti",
]

def fetch_polymarket(state):
    """Fetch finance/macro prediction markets from Polymarket (public API)."""
    url = ("https://gamma-api.polymarket.com/markets"
           "?limit=200&closed=false&order=volume&ascending=false")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA, "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as r:
            data = json.loads(r.read())

        hits = []
        for m in data:
            q = (m.get("question") or "").lower()
            if not any(kw in q for kw in _POLY_KWS):
                continue
            prices_raw   = m.get("outcomePrices", "[]")
            outcomes_raw = m.get("outcomes", "[]")
            try:
                prices   = json.loads(prices_raw)   if isinstance(prices_raw, str)   else prices_raw
                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                p0 = float(prices[0]) * 100 if prices else 50.0
            except Exception:
                p0, outcomes = 50.0, ["Yes", "No"]
            chg_raw = m.get("oneHourPriceChange")
            try:
                chg_1h = float(chg_raw) * 100 if chg_raw else None
            except Exception:
                chg_1h = None
            hits.append({
                "question": m.get("question", "")[:90],
                "prob":     p0,
                "outcomes": outcomes[:2],
                "chg_1h":   chg_1h,
                "volume":   m.get("volumeNum") or 0,
            })

        hits.sort(key=lambda x: -x["volume"])
        with state.lock:
            state.polymarket = hits[:20]
    except Exception:
        pass

# ─── HISTORY (sentiment snapshots for sparklines / correlation) ───────────────

_MAX_SNAPS = 30 * 48   # 30 days × 48 half-hour slots = 1 440 entries max

def load_history(state):
    """Load snapshots from disk; handles old dict and new list format."""
    try:
        if os.path.exists(HIST_FILE):
            with open(HIST_FILE) as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                raw = raw.get("hourly", [])   # migrate old dual-res format
            state.history = raw
    except Exception:
        state.history = []

def save_snapshot(state):
    """Save one snapshot every 30 min; keep up to 30 days of intraday data."""
    scores = compute_sector_scores(state.news)
    if not scores:
        return
    now = datetime.now()
    if state.history:
        try:
            last_dt = datetime.fromisoformat(state.history[-1]["ts"])
            if (now - last_dt).total_seconds() < 1800:   # 30-min throttle
                return
        except Exception:
            pass
    state.history.append({"ts": now.isoformat(), "scores": scores})
    state.history = state.history[-_MAX_SNAPS:]
    try:
        with open(HIST_FILE, "w") as f:
            json.dump(state.history, f)
    except Exception:
        pass

def combined_timeline(state):
    return state.history

def compute_daily_averages(history):
    """Group all snapshots by date; return {date_str: {sector: avg_score}}.
    Dates are sorted chronologically; today's entry is updated live."""
    buckets = defaultdict(lambda: defaultdict(list))
    for snap in history:
        date = snap["ts"][:10]
        for sector, score in snap["scores"].items():
            buckets[date][sector].append(score)
    return {d: {s: int(sum(v) / len(v)) for s, v in sectors.items()}
            for d, sectors in sorted(buckets.items())}

def compute_correlations(timeline, sectors):
    """Pearson correlation matrix over a flat list of {ts, scores} snapshots."""
    n = len(timeline)
    if n < 3:
        return None
    series = {s: [h["scores"].get(s, 50) for h in timeline] for s in sectors}
    corr   = {}
    for s1 in sectors:
        for s2 in sectors:
            if s1 == s2:
                corr[(s1, s2)] = 1.0
                continue
            x, y   = series[s1], series[s2]
            mx, my = sum(x) / n, sum(y) / n
            num    = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
            dx     = math.sqrt(sum((xi - mx) ** 2 for xi in x))
            dy     = math.sqrt(sum((yi - my) ** 2 for yi in y))
            corr[(s1, s2)] = (num / (dx * dy)) if dx and dy else 0.0
    return corr

# ─── DRAWING ─────────────────────────────────────────────────────────────────

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    BG = curses.COLOR_BLACK
    curses.init_pair(C_HEADER,  curses.COLOR_CYAN,    BG)
    curses.init_pair(C_POS,     curses.COLOR_GREEN,   BG)
    curses.init_pair(C_NEG,     curses.COLOR_RED,     BG)
    curses.init_pair(C_NEU,     curses.COLOR_YELLOW,  BG)
    curses.init_pair(C_SEL,     curses.COLOR_BLACK,   curses.COLOR_CYAN)
    curses.init_pair(C_DIM,     curses.COLOR_WHITE,   BG)   # muted white on black
    curses.init_pair(C_TITLE,   curses.COLOR_WHITE,   BG)
    curses.init_pair(C_ACCENT,  curses.COLOR_CYAN,    BG)
    curses.init_pair(C_BAR_POS, curses.COLOR_GREEN,   BG)
    curses.init_pair(C_BAR_NEG, curses.COLOR_RED,     BG)
    curses.init_pair(C_BAR_NEU, curses.COLOR_YELLOW,  BG)
    curses.init_pair(C_BORDER,  curses.COLOR_WHITE,   BG)
    curses.init_pair(C_LOADING, curses.COLOR_YELLOW,  BG)
    curses.init_pair(C_TRUMP,   curses.COLOR_MAGENTA, BG)
    curses.init_pair(C_GEOPOL,  curses.COLOR_RED,     BG)
    curses.init_pair(C_MACRO,   curses.COLOR_YELLOW,  BG)
    curses.init_pair(C_CRYPTO,  curses.COLOR_CYAN,    BG)
    curses.init_pair(C_MKTS,    curses.COLOR_WHITE,   BG)

def safe_addstr(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    avail = w - x - 1
    if avail <= 0:
        return
    try:
        win.addstr(y, x, text[:avail], attr)
    except curses.error:
        pass

def draw_topbar(win, state):
    h, w = win.getmaxyx()
    tabs  = ["News","Sectors","Mkt","Watchlist","Chart"]
    # Fill bar background
    win.attron(curses.color_pair(C_BORDER))
    win.hline(0, 0, " ", w)
    win.attroff(curses.color_pair(C_BORDER))
    # Brand
    safe_addstr(win, 0, 1, " TradeBot ", curses.color_pair(C_HEADER) | curses.A_BOLD)
    safe_addstr(win, 0, 11, "▸", curses.color_pair(C_DIM))
    col = 13
    for i, t in enumerate(tabs):
        label = f" [{i+1}]{t} "
        if i == state.tab:
            safe_addstr(win, 0, col, label, curses.color_pair(C_SEL) | curses.A_BOLD)
        else:
            safe_addstr(win, 0, col, label, curses.color_pair(C_DIM))
        col += len(label)
    ts = datetime.now().strftime(" %H:%M:%S ")
    safe_addstr(win, 0, w - len(ts) - 1, ts, curses.color_pair(C_DIM))

def draw_statusbar(win, state):
    h, w = win.getmaxyx()
    win.attron(curses.color_pair(C_BORDER))
    win.hline(h-1, 0, " ", w)
    win.attroff(curses.color_pair(C_BORDER))
    if state.loading:
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        sp = spinner[int(time.time() * 8) % len(spinner)]
        msg = f" {sp} Fetching {state.feeds_total} feeds in parallel…  [Q] quit"
        safe_addstr(win, h-1, 0, msg[:w-1], curses.color_pair(C_LOADING))
    elif state.error:
        safe_addstr(win, h-1, 0, f" ✗ {state.error}"[:w-1], curses.color_pair(C_NEG))
    else:
        upd  = state.last_update or "?"
        msg  = (f" [↑↓] navigate  [R] refresh  [Q] quit"
                f"   Updated {upd}  ·  {state.feeds_ok}/{state.feeds_total} feeds"
                f"  ·  {len(state.news)} articles"
                f"  ·  Bloomberg · Reuters · AP · CNBC · MarketWatch")
        safe_addstr(win, h-1, 0, msg[:w-1], curses.color_pair(C_DIM))

def signal_color(signal):
    if signal == "bullish": return curses.color_pair(C_POS)
    if signal == "bearish": return curses.color_pair(C_NEG)
    return curses.color_pair(C_NEU)

def score_color(score):
    if score >= 60: return curses.color_pair(C_POS)
    if score <= 40: return curses.color_pair(C_NEG)
    return curses.color_pair(C_NEU)

def theme_color(theme):
    return {
        "geopolitics": curses.color_pair(C_GEOPOL),
        "macro":       curses.color_pair(C_MACRO),
        "crypto":      curses.color_pair(C_CRYPTO),
        "markets":     curses.color_pair(C_MKTS),
    }.get(theme, curses.color_pair(C_DIM))

# ─── TAB 1: NEWS ─────────────────────────────────────────────────────────────

def draw_news(win, state):
    h, w   = win.getmaxyx()
    # Apply sector filter if set
    news   = [a for a in state.news if a.get("sector") == state.sector_filter] \
             if state.sector_filter else state.news
    sel    = state.sel
    scroll = state.scroll
    split  = w * 3 // 5

    # Column layout:  AGE(5) SOURCE(12) SIGNAL(8) THEME(12) HEADLINE(rest)
    # col offsets:    1      8          21        30        43
    filter_label = f"  [filter: {state.sector_filter}  click sector to clear]" \
                   if state.sector_filter else ""
    safe_addstr(win, 1, 1,
        f"{'AGE':<5}  {'SOURCE':<12}  {'SIGNAL':<8}  {'THEME':<12}  HEADLINE",
        curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 1, split - len(filter_label) - 1,
        filter_label, curses.color_pair(C_NEU) | curses.A_BOLD)
    win.hline(2, 0, curses.ACS_HLINE, split - 1)

    content_h = h - 4
    for i, article in enumerate(news[scroll: scroll + content_h]):
        row    = i + 3
        idx    = i + scroll
        is_sel = (idx == sel)

        if row >= h - 1:
            break

        age   = article.get("age","?")[:5]
        src   = article.get("source","")[:11]
        sig   = article.get("signal","neutral")
        theme = article.get("theme","markets")[:11]
        title = article.get("title","")
        avail = split - 44
        is_trump = "trump" in title.lower()

        if is_sel:
            win.hline(row, 0, " ", split - 1)
            base = curses.color_pair(C_SEL)
            safe_addstr(win, row, 1,  f"{age:<5}",    base)
            safe_addstr(win, row, 8,  f"{src:<12}",   base)
            safe_addstr(win, row, 21, f"{sig:<8}",    base | curses.A_BOLD)
            safe_addstr(win, row, 30, f"{theme:<12}", base)
            safe_addstr(win, row, 43, title[:avail],  base)
        else:
            title_attr = (curses.color_pair(C_TRUMP) | curses.A_BOLD
                          if is_trump else curses.color_pair(C_TITLE))
            safe_addstr(win, row, 1,  f"{age:<5}",    curses.color_pair(C_DIM))
            safe_addstr(win, row, 8,  f"{src:<12}",   curses.color_pair(C_ACCENT))
            safe_addstr(win, row, 21, f"{sig:<8}",    signal_color(sig) | curses.A_BOLD)
            safe_addstr(win, row, 30, f"{theme:<12}", theme_color(theme) | curses.A_BOLD)
            safe_addstr(win, row, 43, title[:avail],  title_attr)

    # Vertical divider
    for r in range(1, h-1):
        try:
            win.addch(r, split, curses.ACS_VLINE, curses.color_pair(C_BORDER))
        except curses.error:
            pass

    # ── Detail panel ──
    safe_addstr(win, 1, split+2, "DETAIL", curses.color_pair(C_HEADER) | curses.A_BOLD)
    win.hline(2, split+1, curses.ACS_HLINE, w - split - 2)

    if sel < len(news):
        a     = news[sel]
        title = a.get("title","")
        sig   = a.get("signal","neutral")
        score = a.get("sentiment", 50)
        sect  = a.get("sector","")
        src   = a.get("source","")
        age   = a.get("age","?")
        etfs  = SECTOR_ETFS.get(sect, [])[:4]
        pw    = w - split - 3
        prow  = 3

        # Wrap headline
        words, line = title.split(), ""
        for word in words:
            if len(line) + len(word) + 1 <= pw:
                line += ("" if not line else " ") + word
            else:
                if prow < h - 2:
                    safe_addstr(win, prow, split+2, line, curses.color_pair(C_TITLE) | curses.A_BOLD)
                prow += 1; line = word
        if line and prow < h - 2:
            safe_addstr(win, prow, split+2, line, curses.color_pair(C_TITLE) | curses.A_BOLD)
        prow += 2

        def prow_write(label, val, val_attr=0):
            nonlocal prow
            if prow >= h - 2: return
            safe_addstr(win, prow, split+2, f"{label:<10}", curses.color_pair(C_DIM))
            safe_addstr(win, prow, split+12, val, val_attr or curses.color_pair(C_TITLE))
            prow += 1

        prow_write("Source",  src,                curses.color_pair(C_ACCENT))
        prow_write("Sector",  sect,               curses.color_pair(C_ACCENT))
        prow_write("Signal",  sig.upper(),        signal_color(sig) | curses.A_BOLD)
        prow_write("Age",     age + " ago")

        # Sentiment bar
        if prow + 3 < h - 2:
            prow += 1
            safe_addstr(win, prow, split+2,
                f"Sentiment  {score}/100", score_color(score) | curses.A_BOLD)
            prow += 1
            bar_w  = min(pw - 2, 22)
            filled = int((score / 100) * bar_w)
            safe_addstr(win, prow, split+2,
                "█" * filled + "░" * (bar_w - filled), score_color(score))
            prow += 2

        # Related ETFs
        if etfs and prow + 2 < h - 2:
            safe_addstr(win, prow, split+2, "Related ETFs", curses.color_pair(C_DIM) | curses.A_BOLD)
            prow += 1
            for ticker, name in etfs:
                if prow >= h - 2: break
                safe_addstr(win, prow, split+2, f" {ticker:<6}", curses.color_pair(C_POS) | curses.A_BOLD)
                safe_addstr(win, prow, split+9, name[:pw-8], curses.color_pair(C_DIM))
                prow += 1

# ─── TAB 2: SECTORS ──────────────────────────────────────────────────────────

# Maps terminal row → sector name; rebuilt every draw cycle
_sector_row_map = {}

def draw_sectors(win, state):
    global _sector_row_map
    _sector_row_map = {}
    h, w   = win.getmaxyx()
    scores = compute_sector_scores(state.news)

    safe_addstr(win, 1, 2, "SECTOR SENTIMENT", curses.color_pair(C_HEADER) | curses.A_BOLD)
    safe_addstr(win, 1, 20, "recency-weighted sentiment  [click a sector to filter News tab]",
                curses.color_pair(C_DIM))
    safe_addstr(win, 2, 2,
        "Score 0-100  │  ≥60 ",
        curses.color_pair(C_DIM))
    safe_addstr(win, 2, 23, "bullish", curses.color_pair(C_POS) | curses.A_BOLD)
    safe_addstr(win, 2, 31, "  │  40-60 ", curses.color_pair(C_DIM))
    safe_addstr(win, 2, 42, "neutral", curses.color_pair(C_NEU) | curses.A_BOLD)
    safe_addstr(win, 2, 50, "  │  ≤40 ", curses.color_pair(C_DIM))
    safe_addstr(win, 2, 59, "bearish", curses.color_pair(C_NEG) | curses.A_BOLD)
    win.hline(3, 0, curses.ACS_HLINE, w)

    row, bar_max = 4, w - 32
    for sector, score in scores.items():
        if row >= h - 3: break
        bar_w   = int((score / 100) * bar_max)
        scol    = score_color(score)
        signal  = "▲ bullish" if score >= 60 else "▼ bearish" if score <= 40 else "● neutral"
        sig_col = signal_color("bullish" if score >= 60 else "bearish" if score <= 40 else "neutral")
        is_active = (state.sector_filter == sector)

        _sector_row_map[row] = sector  # register clickable row

        # Highlight the active/selected sector
        name_attr = (curses.color_pair(C_SEL) | curses.A_BOLD) if is_active \
                    else (curses.color_pair(C_TITLE) | curses.A_BOLD)
        safe_addstr(win, row, 2,  f"{sector:<14}", name_attr)
        safe_addstr(win, row, 17, f"{score:>3}/100 ", scol | curses.A_BOLD)
        safe_addstr(win, row, 25, "█" * bar_w,         scol)
        safe_addstr(win, row, 25 + bar_w, "░" * (bar_max - bar_w), curses.color_pair(C_BORDER))
        safe_addstr(win, row, 26 + bar_max, signal, sig_col)
        row += 1

        # Top 2 headlines for this sector
        sector_news = [a for a in state.news if a.get("sector") == sector][:2]
        for a in sector_news:
            if row >= h - 3: break
            sym = "+" if a["signal"]=="bullish" else "-" if a["signal"]=="bearish" else "~"
            safe_addstr(win, row, 4,  sym,                signal_color(a["signal"]) | curses.A_BOLD)
            safe_addstr(win, row, 6,  a["source"][:10],   curses.color_pair(C_ACCENT))
            safe_addstr(win, row, 17, a["title"][:w-19],  curses.color_pair(C_DIM))
            row += 1
        row += 1

# ─── TAB 3: MKT ──────────────────────────────────────────────────────────────

_CAT_COLORS = {
    "equity":    curses.COLOR_GREEN,
    "commodity": curses.COLOR_YELLOW,
    "crypto":    curses.COLOR_CYAN,
    "forex":     curses.COLOR_WHITE,
    "fund":      curses.COLOR_WHITE,
}

def draw_mkt(win, state):
    h, w = win.getmaxyx()
    safe_addstr(win, 1, 2, "MARKET OVERVIEW", curses.color_pair(C_HEADER) | curses.A_BOLD)
    ts_str = f"updated {state.mkt_last_update}" if state.mkt_last_update else "press [R] to load"
    safe_addstr(win, 1, 19, f"{ts_str}  ·  [R] refresh", curses.color_pair(C_DIM))

    # ── Left panel: market quotes ──
    split  = min(75, w * 3 // 5)
    spark_col = 74   # 5D sparkline starts here (if terminal wide enough)

    safe_addstr(win, 2, 2,  f"{'SYM':<8}",   curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 2, 11, f"{'NAME':<22}",  curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 2, 34, f"{'CAT':<10}",   curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 2, 45, f"{'PRICE':>12}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 2, 58, f"{'DAY%':>8}",   curses.color_pair(C_DIM) | curses.A_BOLD)
    if w > spark_col + 5:
        safe_addstr(win, 2, spark_col, "5D", curses.color_pair(C_DIM) | curses.A_BOLD)
    win.hline(3, 0, curses.ACS_HLINE, w)

    if state.mkt_loading:
        msg = "⠿ Fetching market quotes via Yahoo Finance…"
        safe_addstr(win, h // 2, max(0, (w - len(msg)) // 2), msg,
                    curses.color_pair(C_LOADING) | curses.A_BOLD)
        return
    if not state.mkt_data:
        msg = "No market data — press [R] to fetch"
        safe_addstr(win, h // 2, max(0, (w - len(msg)) // 2), msg,
                    curses.color_pair(C_DIM))
        return

    row = 4
    for item in state.mkt_data:
        if row >= h - 1:
            break
        sym   = item["symbol"]
        name  = item["name"]
        cat   = item["category"]
        price = item["price"]
        chg   = item["chg_pct"]
        closes = item.get("closes", [])

        if chg is None:
            chg_col = curses.color_pair(C_DIM);  chg_str = "     ---"
        elif chg > 0:
            chg_col = curses.color_pair(C_POS);  chg_str = f"+{chg:.2f}%"
        elif chg < 0:
            chg_col = curses.color_pair(C_NEG);  chg_str = f"{chg:.2f}%"
        else:
            chg_col = curses.color_pair(C_NEU);  chg_str = f" {chg:.2f}%"

        cat_attr = {
            "equity":    curses.color_pair(C_POS),
            "commodity": curses.color_pair(C_NEU),
            "crypto":    curses.color_pair(C_ACCENT),
            "forex":     curses.color_pair(C_DIM),
            "fund":      curses.color_pair(C_DIM),
        }.get(cat, curses.color_pair(C_TITLE))

        price_str = f"{price:,.2f}" if price is not None else "---"

        safe_addstr(win, row, 2,  f"{sym:<8}",       cat_attr | curses.A_BOLD)
        safe_addstr(win, row, 11, f"{name:<22}",      curses.color_pair(C_TITLE))
        safe_addstr(win, row, 34, f"{cat:<10}",       curses.color_pair(C_DIM))
        safe_addstr(win, row, 45, f"{price_str:>12}", curses.color_pair(C_TITLE))
        safe_addstr(win, row, 58, f"{chg_str:>8}",    chg_col | curses.A_BOLD)

        # 5-day sparkline from close prices
        if w > spark_col + 5 and len(closes) >= 2:
            mn, mx = min(closes), max(closes)
            rng    = mx - mn or 1
            spark  = "".join(_SPARK[max(0, min(8, int((c - mn) / rng * 8.99)))]
                             for c in closes)
            safe_addstr(win, row, spark_col, spark, chg_col)

        row += 1

    # ── Polymarket prediction markets panel ──
    if not state.polymarket:
        return

    div_row = row + 1
    if div_row >= h - 4:
        return
    win.hline(div_row, 0, curses.ACS_HLINE, w)
    safe_addstr(win, div_row + 1, 2, "PREDICTION MARKETS",
                curses.color_pair(C_HEADER) | curses.A_BOLD)
    safe_addstr(win, div_row + 1, 22, "via Polymarket (public)",
                curses.color_pair(C_DIM))
    safe_addstr(win, div_row + 2, 2,  f"{'PROB':>6}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, div_row + 2, 10, f"{'1H Δ':>6}",  curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, div_row + 2, 18, "QUESTION",        curses.color_pair(C_DIM) | curses.A_BOLD)
    prow = div_row + 3
    for pm in state.polymarket:
        if prow >= h - 1:
            break
        prob  = pm["prob"]
        chg1h = pm["chg_1h"]
        q_str = pm["question"][:w - 20]

        if prob >= 60:   p_col = curses.color_pair(C_POS)
        elif prob <= 40: p_col = curses.color_pair(C_NEG)
        else:            p_col = curses.color_pair(C_NEU)

        if chg1h is None:
            c_str = "    ---";  c_col = curses.color_pair(C_DIM)
        elif chg1h > 0:
            c_str = f"+{chg1h:.1f}%"; c_col = curses.color_pair(C_POS)
        elif chg1h < 0:
            c_str = f"{chg1h:.1f}%";  c_col = curses.color_pair(C_NEG)
        else:
            c_str = " 0.0%";           c_col = curses.color_pair(C_NEU)

        safe_addstr(win, prow, 2,  f"{prob:5.1f}%",  p_col | curses.A_BOLD)
        safe_addstr(win, prow, 10, f"{c_str:>6}",    c_col)
        safe_addstr(win, prow, 18, q_str,             curses.color_pair(C_TITLE))
        prow += 1

# ─── TAB 4: WATCHLIST ────────────────────────────────────────────────────────

def draw_watchlist(win, state):
    h, w  = win.getmaxyx()
    scores = compute_sector_scores(state.news)
    recs   = compute_etf_recommendations(scores)

    safe_addstr(win, 1, 2, "ETF WATCHLIST", curses.color_pair(C_HEADER) | curses.A_BOLD)
    safe_addstr(win, 1, 17, "conviction from news signal strength", curses.color_pair(C_DIM))
    win.hline(2, 0, curses.ACS_HLINE, w)
    safe_addstr(win, 3, 2,  f"{'TICKER':<8}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 3, 11, f"{'NAME':<36}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 3, 48, f"{'SECTOR':<14}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 3, 63, f"{'CONV':<7}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 3, 70, "SIGNAL", curses.color_pair(C_DIM) | curses.A_BOLD)
    win.hline(4, 0, curses.ACS_HLINE, w)

    scroll  = state.etf_scroll
    visible = recs[scroll: scroll + h - 7]
    for i, r in enumerate(visible):
        row  = 5 + i
        if row >= h - 1: break
        score = r["score"]
        sent  = r["sentiment"]
        scol  = score_color(sent)
        filled = int(round(score))
        stars  = "★" * filled + "☆" * (5 - filled)

        safe_addstr(win, row, 2,  r["ticker"],         curses.color_pair(C_ACCENT) | curses.A_BOLD)
        safe_addstr(win, row, 11, r["name"][:35],      curses.color_pair(C_TITLE))
        safe_addstr(win, row, 48, r["sector"][:13],    curses.color_pair(C_DIM))
        safe_addstr(win, row, 63, f"{score:.1f}",      scol | curses.A_BOLD)
        safe_addstr(win, row, 70, stars,               scol)

# ─── TAB 5: CHART ────────────────────────────────────────────────────────────

_SPARK = " ▁▂▃▄▅▆▇█"   # 9 levels (index 0–8)

def _spark_char(val):
    return _SPARK[max(0, min(8, int(val / 100 * 8.99)))]

def draw_chart(win, state):
    h, w   = win.getmaxyx()
    scores = compute_sector_scores(state.news)
    recs   = compute_etf_recommendations(scores)[:8]
    items  = list(scores.items())
    sectors = list(SECTOR_KEYWORDS.keys())

    n_snaps   = len(state.history)
    n_days    = len(set(s["ts"][:10] for s in state.history)) if state.history else 0
    snap_info = f"{n_snaps} snapshots · {n_days}d · 30-min cadence"
    safe_addstr(win, 1, 2, "SECTOR CHART", curses.color_pair(C_HEADER) | curses.A_BOLD)
    safe_addstr(win, 1, 16, f"  ({snap_info})", curses.color_pair(C_DIM))
    win.hline(2, 0, curses.ACS_HLINE, w)

    # ── [A] Scatter / line plot of current sentiment ──
    plot_h   = min((h - 8) // 3, 12)
    n_sect   = len(items)
    col_w    = max(5, (w - 8) // max(n_sect, 1))
    axis_col = 5       # y-axis at column 5
    data_col = axis_col + 1

    safe_addstr(win, 3, 2, "Current Sentiment  (◆ = sector score, ─── = neutral 50)",
                curses.color_pair(C_DIM))

    # Y-axis ticks
    for r in range(plot_h + 1):
        val  = 100 - int(r / plot_h * 100)
        prow = 4 + r
        if prow >= h - 1:
            break
        if val % 20 == 0:
            safe_addstr(win, prow, 2,  f"{val:>3}", curses.color_pair(C_DIM))
            safe_addstr(win, prow, axis_col, "┤", curses.color_pair(C_BORDER))
        else:
            safe_addstr(win, prow, axis_col, "│", curses.color_pair(C_BORDER))

    # Neutral line at score = 50
    neutral_row = 4 + int((100 - 50) / 100 * plot_h)
    for nc in range(data_col, data_col + n_sect * col_w + 2):
        safe_addstr(win, neutral_row, nc, "─", curses.color_pair(C_DIM))

    # Plot each sector as ◆ with score label; draw connecting dashes between dots
    prev_col = prev_dot_row = None
    for i, (sector, score) in enumerate(items):
        col     = data_col + i * col_w + col_w // 2
        dot_row = 4 + max(0, min(plot_h, int((100 - score) / 100 * plot_h)))
        scol    = score_color(score)
        if dot_row < h - 1:
            safe_addstr(win, dot_row, col, "◆", scol | curses.A_BOLD)
            safe_addstr(win, dot_row, col + 1, f"{score}", scol)
            # Connect consecutive dots with dashes
            if prev_col is not None and prev_dot_row is not None:
                step_r = (dot_row - prev_dot_row)
                step_c = col - prev_col
                steps  = max(abs(step_r), abs(step_c), 1)
                for s in range(1, steps):
                    cr = prev_dot_row + int(step_r * s / steps)
                    cc = prev_col     + int(step_c * s / steps)
                    if 4 <= cr < h - 1 and cc > axis_col:
                        safe_addstr(win, cr, cc, "·", curses.color_pair(C_DIM))
            prev_col, prev_dot_row = col, dot_row
        # Sector label at x-axis
        lbl_row = 4 + plot_h + 1
        if lbl_row < h - 1:
            safe_addstr(win, lbl_row, data_col + i * col_w,
                        sector[:col_w - 1], curses.color_pair(C_DIM))

    # X-axis
    ax_row = 4 + plot_h
    if ax_row < h - 1:
        safe_addstr(win, ax_row, axis_col,
                    "┴" + "─" * (n_sect * col_w + 2), curses.color_pair(C_BORDER))

    div1 = 4 + plot_h + 3

    # ── [B] Sector daily average grid ──
    daily_avgs  = compute_daily_averages(state.history)
    today_iso   = datetime.now().date().isoformat()
    all_dates   = sorted(daily_avgs.keys())
    label_w     = 13
    col_w       = 5                              # " nn  " per day
    cols_fit    = max(1, (w - label_w - 4) // col_w)
    dates_shown = all_dates[-cols_fit:]

    if div1 < h - 4:
        win.hline(div1, 0, curses.ACS_HLINE, w)
        n_days_str = f"{len(all_dates)}d · {n_snaps} snapshots"
        safe_addstr(win, div1 + 1, 2,
                    f"Daily Avg Sentiment  ({n_days_str})",
                    curses.color_pair(C_DIM) | curses.A_BOLD)

        if not daily_avgs:
            safe_addstr(win, div1 + 2, 2,
                        "First snapshot in ~30 min after startup.",
                        curses.color_pair(C_DIM))
        else:
            # Date header
            hrow = div1 + 2
            if hrow < h - 1:
                col = label_w + 4
                for d in dates_shown:
                    lbl = "Today" if d == today_iso else d[5:]  # "MM-DD"
                    safe_addstr(win, hrow, col, f"{lbl:>{col_w}}",
                                curses.color_pair(C_DIM) | curses.A_BOLD)
                    col += col_w
            # Sector rows
            for ri, sector in enumerate(sectors):
                rrow = div1 + 3 + ri
                if rrow >= h - 2:
                    break
                safe_addstr(win, rrow, 2, f"{sector:<{label_w}}", curses.color_pair(C_DIM))
                col = label_w + 4
                for d in dates_shown:
                    sc = daily_avgs.get(d, {}).get(sector)
                    if sc is not None:
                        safe_addstr(win, rrow, col, f"{sc:>{col_w}}",
                                    score_color(sc) | curses.A_BOLD)
                    else:
                        safe_addstr(win, rrow, col, f"{'—':>{col_w}}",
                                    curses.color_pair(C_BORDER))
                    col += col_w

    div2 = div1 + 3 + len(sectors) + 2

    # ── [C] ETF daily conviction grid ──
    if div2 < h - 4 and daily_avgs:
        win.hline(div2, 0, curses.ACS_HLINE, w)
        safe_addstr(win, div2 + 1, 2,
                    "Daily Avg ETF Conviction  (top ETF per sector, score 0-5★)",
                    curses.color_pair(C_DIM) | curses.A_BOLD)
        # Date header
        hrow = div2 + 2
        if hrow < h - 1:
            col = label_w + 4
            for d in dates_shown:
                lbl = "Today" if d == today_iso else d[5:]
                safe_addstr(win, hrow, col, f"{lbl:>{col_w}}",
                            curses.color_pair(C_DIM) | curses.A_BOLD)
                col += col_w
        # One row per sector: ticker (top ETF) + daily conviction
        for ri, sector in enumerate(sectors):
            rrow = div2 + 3 + ri
            if rrow >= h - 2:
                break
            etf_ticker = SECTOR_ETFS.get(sector, [("---","")])[0][0]
            label = f"{etf_ticker:<6}{sector[:6]}"
            safe_addstr(win, rrow, 2, f"{label:<{label_w}}", curses.color_pair(C_ACCENT))
            col = label_w + 4
            for d in dates_shown:
                sc   = daily_avgs.get(d, {}).get(sector)
                conv = round((sc / 100) * 5, 1) if sc is not None else None
                if conv is not None:
                    safe_addstr(win, rrow, col, f"{conv:>{col_w}.1f}",
                                score_color(sc) | curses.A_BOLD)
                else:
                    safe_addstr(win, rrow, col, f"{'—':>{col_w}}",
                                curses.color_pair(C_BORDER))
                col += col_w

    div3 = div2 + 3 + len(sectors) + 2

    # ── [D] Sector correlation matrix ──
    timeline = combined_timeline(state)
    corr     = compute_correlations(timeline, sectors)
    if div3 < h - 3:
        win.hline(div3, 0, curses.ACS_HLINE, w)
        div2 = div3   # reuse variable for the block below
        if corr is None:
            safe_addstr(win, div2 + 1, 2,
                        "Correlation: need ≥3 snapshots — builds after ~1 h.",
                        curses.color_pair(C_DIM))
        else:
            safe_addstr(win, div2 + 1, 2,
                        "Sector Correlation  (Pearson r · green≥+0.5 · red≤-0.5)",
                        curses.color_pair(C_DIM) | curses.A_BOLD)
            abbr    = [s[:4] for s in sectors]
            hdr_row = div2 + 2
            if hdr_row < h - 1:
                col = 16
                for a in abbr:
                    safe_addstr(win, hdr_row, col, f"{a:>5}",
                                curses.color_pair(C_DIM) | curses.A_BOLD)
                    col += 5
            for ri, s1 in enumerate(sectors):
                rrow = div2 + 3 + ri
                if rrow >= h - 1:
                    break
                safe_addstr(win, rrow, 2, f"{s1:<13}", curses.color_pair(C_DIM))
                col = 16
                for s2 in sectors:
                    r_val = corr.get((s1, s2), 0.0)
                    r_str = f"{r_val:+.2f}"
                    if s1 == s2:
                        attr = curses.color_pair(C_DIM) | curses.A_BOLD
                    elif r_val >= 0.5:
                        attr = curses.color_pair(C_POS) | curses.A_BOLD
                    elif r_val <= -0.5:
                        attr = curses.color_pair(C_NEG) | curses.A_BOLD
                    else:
                        attr = curses.color_pair(C_NEU)
                    safe_addstr(win, rrow, col, f"{r_str:>5}", attr)
                    col += 5

# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(200)
    init_colors()
    # Force black background across entire screen
    stdscr.bkgd(' ', curses.color_pair(C_TITLE))
    # Enable mouse clicks
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

    state = AppState()
    load_history(state)   # load stored sentiment snapshots from disk

    def bg_fetch():
        fetch_all_news(state)
        save_snapshot(state)

    t = threading.Thread(target=bg_fetch, daemon=True)
    t.start()
    # Fetch Polymarket on startup (background, non-blocking)
    threading.Thread(target=fetch_polymarket, args=(state,), daemon=True).start()

    last_refresh = time.time()

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        with state.lock:
            draw_topbar(stdscr, state)
            if state.loading and state.tab != 2:
                msg = "⠿ Fetching news feeds in parallel… please wait"
                safe_addstr(stdscr, h//2,
                    max(0, (w - len(msg)) // 2), msg,
                    curses.color_pair(C_LOADING) | curses.A_BOLD)
            else:
                if   state.tab == 0: draw_news(stdscr, state)
                elif state.tab == 1: draw_sectors(stdscr, state)
                elif state.tab == 2: draw_mkt(stdscr, state)
                elif state.tab == 3: draw_watchlist(stdscr, state)
                elif state.tab == 4: draw_chart(stdscr, state)
            draw_statusbar(stdscr, state)

        stdscr.refresh()
        key = stdscr.getch()

        if key in (ord('q'), ord('Q')):
            break
        elif key == ord('1'): state.tab = 0
        elif key == ord('2'): state.tab = 1
        elif key == ord('3'):
            state.tab = 2
            # Lazy-load market data on first visit
            if not state.mkt_data and not state.mkt_loading:
                threading.Thread(target=fetch_mkt_data, args=(state,), daemon=True).start()
        elif key == ord('4'): state.tab = 3
        elif key == ord('5'): state.tab = 4

        elif key == curses.KEY_MOUSE:
            try:
                _, mx, my, _, bstate = curses.getmouse()
                if bstate & curses.BUTTON1_CLICKED or bstate & curses.BUTTON1_PRESSED:
                    # ── Top bar tab clicks ──
                    if my == 0:
                        # " [1]News "[9] " [2]Sectors "[12] " [3]Mkt "[9] " [4]Watchlist "[14] " [5]Chart "[11]
                        tabs_start = [13, 22, 34, 43, 57]
                        for i, col in enumerate(tabs_start):
                            if mx >= col and (i == len(tabs_start)-1 or mx < tabs_start[i+1]):
                                if i == 2 and not state.mkt_data and not state.mkt_loading:
                                    threading.Thread(target=fetch_mkt_data, args=(state,), daemon=True).start()
                                state.tab = i
                                break

                    # ── News tab: click a row to select it ──
                    elif state.tab == 0 and my >= 3:
                        news_shown = [a for a in state.news
                                      if a.get("sector") == state.sector_filter] \
                                     if state.sector_filter else state.news
                        idx = state.scroll + (my - 3)
                        if 0 <= idx < len(news_shown):
                            state.sel = idx

                    # ── Sectors tab: click a sector row to filter News ──
                    elif state.tab == 1:
                        sector = _sector_row_map.get(my)
                        if sector:
                            # Toggle: clicking active filter clears it
                            if state.sector_filter == sector:
                                state.sector_filter = None
                            else:
                                state.sector_filter = sector
                                state.sel    = 0
                                state.scroll = 0
                                state.tab    = 0   # jump to News tab filtered
            except curses.error:
                pass

        elif key == curses.KEY_UP:
            if state.tab == 0:
                state.sel = max(0, state.sel - 1)
                if state.sel < state.scroll:
                    state.scroll = state.sel
            elif state.tab == 3:   # Watchlist
                state.etf_scroll = max(0, state.etf_scroll - 1)

        elif key == curses.KEY_DOWN:
            if state.tab == 0:
                state.sel = min(max(0, len(state.news)-1), state.sel + 1)
                if state.sel >= state.scroll + (h - 5):
                    state.scroll = state.sel - (h - 6)
            elif state.tab == 3:   # Watchlist
                state.etf_scroll += 1

        elif key in (ord('r'), ord('R')):
            if state.tab == 2:
                if not state.mkt_loading:
                    threading.Thread(target=fetch_mkt_data, args=(state,), daemon=True).start()
                threading.Thread(target=fetch_polymarket, args=(state,), daemon=True).start()
            elif not state.loading:
                state.loading = True
                state.news    = []
                threading.Thread(target=bg_fetch, daemon=True).start()

        # Auto-refresh every 5 minutes
        if time.time() - last_refresh > 300 and not state.loading:
            last_refresh = time.time()
            state.loading = True
            threading.Thread(target=bg_fetch, daemon=True).start()
            if not state.mkt_loading:
                threading.Thread(target=fetch_mkt_data, args=(state,), daemon=True).start()
            threading.Thread(target=fetch_polymarket, args=(state,), daemon=True).start()

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    print("\nTradeBot closed. Run again with: python3 sectornews.py\n")
