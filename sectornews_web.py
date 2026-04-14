#!/usr/bin/env python3
"""
sectornews web - browser-based sector intelligence dashboard
Same data pipeline as sectornews.py, served over HTTP.
"""

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
import http.cookiejar as _cookiejar
import math
import subprocess
import urllib.parse
from datetime import datetime, timezone
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 3000

# Chrome User-Agent — same trick pftui uses to bypass feed restrictions
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# SSL context that accepts all certs (avoids macOS cert-store issues)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ─── RSS FEEDS ───────────────────────────────────────────────────────────────

RSS_FEEDS = [
    # Bloomberg (free public RSS — the same feeds pftui uses)
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
    # Investopedia
    ("Investopedia", "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_headline"),
    # Yahoo Finance
    ("Yahoo",        "https://finance.yahoo.com/news/rssindex"),
    # Seeking Alpha
    ("Seeking Alpha","https://seekingalpha.com/feed.xml"),
]

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

BULLISH_PHRASES = [
    "rate cut","rate cuts","ceasefire","peace deal","earnings beat","dovish",
    "quantitative easing","record high","better than expected","trade deal",
    "rate reduction","debt ceiling resolved","stimulus","budget surplus",
]
BEARISH_PHRASES = [
    "rate hike","hawkish","earnings miss","bank failure","recession fears",
    "mass layoffs","war escalates","quantitative tightening","trade war",
    "debt crisis","stagflation","credit crunch","defaults surge","bank run",
]
POSITIVE_WORDS = [
    "surge","soar","rise","gain","rally","jump","climb","boost","record","high",
    "growth","profit","beat","strong","bullish","upside","expand","recovery",
    "approve","buy","upgrade","positive","optimism","acceleration","demand",
    "breakthrough","secure","win","deal","partnership","increase","thrive",
]
NEGATIVE_WORDS = [
    "fall","drop","slump","crash","plunge","decline","loss","miss","weak",
    "bearish","risk","warn","collapse","fail","downgrade","uncertainty",
    "concern","threat","tension","conflict","tariff","sanction","ban","restrict",
    "recession","contraction","default","debt","crisis","investigation","probe",
]

THEME_KEYWORDS = {
    "geopolitics": ["war","military","nato","sanction","ceasefire","treaty","missile",
                    "troops","invasion","conflict","geopolit","diplomacy","iran","ukraine",
                    "taiwan","china","russia","israel","pentagon","nuclear"],
    "macro":       ["fed","federal reserve","rate cut","rate hike","inflation","gdp",
                    "unemployment","jobs","payroll","cpi","pce","treasury","yield",
                    "recession","central bank","monetary","fiscal","deficit","debt ceiling",
                    "boe","ecb","imf","world bank","fomc","interest rate"],
    "crypto":      ["bitcoin","btc","ethereum","eth","crypto","blockchain","defi","nft",
                    "stablecoin","coinbase","binance","solana","web3","altcoin","token",
                    "mining","crypto exchange","digital asset"],
    "markets":     ["stock","equity","s&p","nasdaq","ipo","merger","earnings","acquisition",
                    "dividend","buyback","sp500","dow jones","russell","hedge fund",
                    "options","futures","etf","portfolio","rally","selloff"],
}

def classify_theme(text):
    t = text.lower()
    best, best_n = "markets", 0
    for theme, kws in THEME_KEYWORDS.items():
        n = sum(1 for kw in kws if kw in t)
        if n > best_n:
            best, best_n = theme, n
    return best

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

_POLY_KWS = [
    "fed","rate cut","rate hike","interest rate","recession","gdp","inflation","cpi",
    "tariff","dollar","gold","oil","nasdaq","s&p","crypto","bitcoin","btc","eth",
    "ethereum","economy","debt","treasury","iran","ukraine","war","ceasefire","trump",
    "china","trade","powell","fiscal","deficit","opec","brent","wti",
]

# ─── DATA PIPELINE ───────────────────────────────────────────────────────────

def fetch_feed(source, url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        })
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            data = r.read()
        root = ET.fromstring(data)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        articles = []
        for item in items[:8]:
            title = (item.findtext("title") or
                     item.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            title = re.sub(r"<[^>]+>", "", title)
            pub   = (item.findtext("pubDate") or
                     item.findtext("{http://www.w3.org/2005/Atom}published") or "")
            link  = (item.findtext("link") or
                     item.findtext("{http://www.w3.org/2005/Atom}id") or "")
            if title:
                articles.append({"source": source, "title": title, "pub": pub, "link": link})
        return articles
    except Exception:
        return []

def score_sentiment(text):
    t = text.lower()
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
            if diff < 60:    return f"{diff}s"
            if diff < 3600:  return f"{diff//60}m"
            if diff < 86400: return f"{diff//3600}h"
            return f"{diff//86400}d"
        except Exception:
            pass
    return "?"

def _age_secs(age_str):
    try:
        if age_str.endswith('s'): return int(age_str[:-1])
        if age_str.endswith('m'): return int(age_str[:-1]) * 60
        if age_str.endswith('h'): return int(age_str[:-1]) * 3600
        if age_str.endswith('d'): return int(age_str[:-1]) * 86400
    except: pass
    return 86400

def enrich(article):
    text = article["title"]
    article["sentiment"] = score_sentiment(text)
    article["sector"]    = classify_sector(text)
    article["theme"]     = classify_theme(text)
    article["age"]       = format_age(article.get("pub",""))
    article["signal"]    = ("bullish" if article["sentiment"] >= 60
                            else "bearish" if article["sentiment"] <= 40
                            else "neutral")
    article["is_trump"]  = "trump" in text.lower()
    return article

def compute_sector_scores(news):
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
            recs.append({"ticker": ticker, "name": name,
                         "sector": sector, "score": conviction, "sentiment": score})
    return sorted(recs, key=lambda x: -x["score"])

def fetch_all_news(state):
    results = [[] for _ in RSS_FEEDS]

    def fetch_one(i, source, url):
        results[i] = [enrich(a) for a in fetch_feed(source, url)]

    threads = [threading.Thread(target=fetch_one, args=(i, src, url), daemon=True)
               for i, (src, url) in enumerate(RSS_FEEDS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    all_articles = [a for batch in results for a in batch]
    all_articles.sort(key=lambda x: _age_secs(x.get("age","?")))
    with state["lock"]:
        state["news"]        = all_articles[:60]
        state["loading"]     = False
        state["last_update"] = datetime.now().strftime("%H:%M:%S")
        state["error"]       = None if all_articles else "No articles loaded (check internet)"
    _save_snapshot_web(state)

# ─── HISTORY / SNAPSHOT ───────────────────────────────────────────────────────

HIST_FILE_WEB = os.path.expanduser("~/.sectornews_web_history.json")
_MAX_SNAPS = 30 * 48  # 1440

def _load_history_web():
    try:
        if os.path.exists(HIST_FILE_WEB):
            with open(HIST_FILE_WEB) as f:
                return json.load(f)
    except Exception:
        pass
    return []

def _save_snapshot_web(state):
    with state["lock"]:
        news = list(state["news"])
    scores = compute_sector_scores(news)
    if not scores:
        return
    now = datetime.now()
    with state["lock"]:
        hist = state["history"]
        if hist:
            try:
                last_dt = datetime.fromisoformat(hist[-1]["ts"])
                if (now - last_dt).total_seconds() < 1800:
                    return
            except Exception:
                pass
        hist.append({"ts": now.isoformat(), "scores": scores})
        state["history"] = hist[-_MAX_SNAPS:]
        hist_to_save = list(state["history"])
    try:
        with open(HIST_FILE_WEB, "w") as f:
            json.dump(hist_to_save, f)
    except Exception:
        pass

# ─── YAHOO FINANCE MARKET DATA ────────────────────────────────────────────────

def _fetch_chart(ticker, retries=2):
    """Yahoo Finance v7 chart via curl — avoids Python urllib 429 fingerprinting."""
    sym_enc = urllib.parse.quote(ticker)
    url = (f"https://query1.finance.yahoo.com/v7/finance/chart/{sym_enc}"
           f"?range=5d&interval=1d")
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ["curl", "-s", "-m", "10", "-A", "Mozilla/5.0", url],
                capture_output=True, timeout=12,
            )
            d = json.loads(r.stdout)
            res = d["chart"]["result"][0]
            meta = res["meta"]
            closes = [c for c in res["indicators"]["quote"][0].get("close", []) if c]
            price = meta.get("regularMarketPrice")
            prev  = meta.get("chartPreviousClose") or (closes[-2] if len(closes) >= 2 else None)
            chg   = round(((price - prev) / prev * 100), 2) if price and prev else None
            price = round(price, 2) if price else None
            return price, chg, closes
        except Exception:
            if attempt < retries - 1:
                time.sleep(1.0)
    return None, None, []

def fetch_mkt_data(state):
    """Fetch sequentially with 0.4s gaps to stay under Yahoo rate limits."""
    rows = []
    for display, name, ticker, cat in MKT_SYMBOLS:
        price, chg, closes = _fetch_chart(ticker)
        rows.append({"symbol": display, "name": name, "ticker": ticker,
                     "category": cat, "price": price, "chg_pct": chg, "closes": closes})
        time.sleep(0.4)
    with state["lock"]:
        state["mkt_data"]    = rows
        state["mkt_loading"] = False
        state["mkt_updated"] = datetime.now().strftime("%H:%M:%S")

# ─── POLYMARKET ───────────────────────────────────────────────────────────────

def fetch_polymarket(state):
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
                outcomes = json.loads(outcomes_raw)  if isinstance(outcomes_raw, str) else outcomes_raw
                p0 = float(prices[0]) * 100 if prices else 50.0
            except Exception:
                p0, outcomes = 50.0, ["Yes", "No"]
            chg_raw = m.get("oneHourPriceChange")
            try:
                chg_1h = float(chg_raw) * 100 if chg_raw else None
            except Exception:
                chg_1h = None
            end_date = (m.get("endDate") or m.get("end_date_iso") or "")[:10]
            hits.append({
                "question": m.get("question", "")[:90],
                "prob":     round(p0, 1),
                "outcome":  outcomes[0] if outcomes else "Yes",
                "chg_1h":   round(chg_1h, 1) if chg_1h is not None else None,
                "volume":   round(m.get("volumeNum") or 0),
                "end_date": end_date,
            })
        hits.sort(key=lambda x: -x["volume"])
        with state["lock"]:
            state["polymarket"] = hits[:25]
    except Exception:
        pass

# ─── CORRELATION ──────────────────────────────────────────────────────────────

def compute_correlation(sector_scores_history):
    """Pearson correlation among sectors over snapshot history."""
    sectors = list(SECTOR_KEYWORDS.keys())
    series = {s: [] for s in sectors}
    for snap in sector_scores_history:
        sc = snap.get("scores", {})
        for s in sectors:
            series[s].append(sc.get(s, 50))
    def pearson(a, b):
        n = len(a)
        if n < 2: return 0.0
        ma, mb = sum(a)/n, sum(b)/n
        num = sum((a[i]-ma)*(b[i]-mb) for i in range(n))
        da  = math.sqrt(sum((x-ma)**2 for x in a))
        db  = math.sqrt(sum((x-mb)**2 for x in b))
        if da == 0 or db == 0: return 0.0
        return round(num/(da*db), 2)
    matrix = {}
    for s1 in sectors:
        matrix[s1] = {}
        for s2 in sectors:
            matrix[s1][s2] = pearson(series[s1], series[s2])
    return {"sectors": sectors, "matrix": matrix}

# ─── HTML TEMPLATE ───────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>TradeBot</title>
<style>
/* ── Reset & base ─────────────────────────────────────────────────────── */
* { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
html, body { height: 100%; }
body {
  background: #0d0d0d; color: #c8c8c8;
  font-family: 'Menlo','Monaco','Consolas',monospace;
  font-size: 13px; line-height: 1.4;
  /* room for fixed bottom nav on mobile */
  padding-bottom: env(safe-area-inset-bottom);
}

/* ── Top bar (desktop) ────────────────────────────────────────────────── */
#topbar {
  background: #111; border-bottom: 1px solid #222;
  display: flex; align-items: center; flex-wrap: nowrap;
  padding: 6px 12px; gap: 2px;
  position: sticky; top: 0; z-index: 100;
}
#topbar .brand { color: #00bcd4; font-weight: bold; margin-right: 12px; white-space: nowrap; }
.dtab { padding: 5px 10px; cursor: pointer; color: #555; border-radius: 3px; white-space: nowrap; font-size: 12px; }
.dtab.active { background: #00bcd4; color: #000; font-weight: bold; }
.dtab:hover:not(.active) { color: #aaa; }
#status { margin-left: auto; color: #444; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 340px; }

/* ── Main content area ────────────────────────────────────────────────── */
#main { padding: 10px 12px; }
.panel { display: none; }
.panel.active { display: block; }

/* ── Signal & score colors ────────────────────────────────────────────── */
.sig-bullish { color: #4caf50; font-weight: bold; }
.sig-bearish { color: #f44336; font-weight: bold; }
.sig-neutral  { color: #ffb300; font-weight: bold; }
.c-pos { color: #4caf50; }
.c-neg { color: #f44336; }
.c-neu { color: #ffb300; }
.bg-pos { background: #4caf50; }
.bg-neg { background: #f44336; }
.bg-neu { background: #ffb300; }
.loading-msg { color: #555; padding: 40px; text-align: center; }

/* ── News tab ─────────────────────────────────────────────────────────── */
#news-wrap { display: flex; gap: 12px; align-items: flex-start; }
#news-list-col { flex: 2; min-width: 0; }
#news-detail {
  flex: 1; min-width: 240px; background: #111;
  border: 1px solid #222; border-radius: 6px;
  padding: 14px; position: sticky; top: 48px;
}
#news-detail h3 { color: #e0e0e0; margin-bottom: 12px; font-size: 13px; line-height: 1.6; font-weight: normal; }
.detail-row { display: flex; margin-bottom: 7px; gap: 8px; }
.detail-label { color: #555; min-width: 72px; flex-shrink: 0; font-size: 11px; }
.detail-val { color: #c8c8c8; }
.sent-bar-bg { background: #222; border-radius: 3px; height: 8px; margin-top: 6px; }
.sent-bar-fill { height: 8px; border-radius: 3px; transition: width 0.3s; }
.etf-list { margin-top: 12px; border-top: 1px solid #1e1e1e; padding-top: 10px; }
.etf-list h4 { color: #444; margin-bottom: 8px; font-weight: normal; font-size: 11px; letter-spacing: .04em; }
.etf-row { display: flex; gap: 8px; margin-bottom: 5px; align-items: baseline; }
.etf-ticker { color: #4caf50; font-weight: bold; min-width: 46px; }
.etf-name { color: #555; font-size: 11px; }

#news-table { width: 100%; border-collapse: collapse; }
#news-table th { color: #444; text-align: left; padding: 5px 8px; border-bottom: 1px solid #1e1e1e; font-weight: normal; font-size: 11px; }
#news-table td { padding: 7px 8px; border-bottom: 1px solid #161616; vertical-align: top; cursor: pointer; }
#news-table tr:hover td { background: #141f22; }
#news-table tr.selected td { background: #0a2730; }
.col-age { color: #444; width: 42px; font-size: 11px; }
.col-src { color: #00bcd4; width: 90px; }
.col-sect { width: 95px; color: #888; font-size: 11px; }
.col-theme { width: 90px; font-size: 11px; }

/* ── Mkt tab ──────────────────────────────────────────────────────────── */
#mkt-body { display: flex; flex-direction: column; gap: 20px; }
.mkt-section h3 { color: #555; font-size: 11px; text-transform: uppercase;
                  letter-spacing: 1px; margin-bottom: 6px; }
#mkt-table, #poly-table { width: 100%; border-collapse: collapse; }
#mkt-table th, #poly-table th {
  color: #555; font-size: 11px; padding: 4px 8px;
  border-bottom: 1px solid #222; cursor: pointer; white-space: nowrap;
  user-select: none;
}
#mkt-table th:hover, #poly-table th:hover { color: #aaa; }
#mkt-table td, #poly-table td { padding: 5px 8px; border-bottom: 1px solid #111; font-size: 12px; }
#mkt-table tr:hover td, #poly-table tr:hover td { background: #141f22; }
.cat-label { color: #444; font-size: 10px; }
.mkt-price { color: #e0e0e0; font-weight: bold; }
.chg-pos { color: #4caf50; }
.chg-neg { color: #f44336; }
.chg-neu { color: #888; }
.sparkline { display: inline-flex; align-items: flex-end; gap: 1px; height: 18px; }
.spark-bar { width: 4px; background: #555; border-radius: 1px 1px 0 0; }
/* Filter badge */
#news-filter-bar { display:flex; align-items:center; gap:8px; padding: 4px 0 8px;
                   font-size: 11px; color: #888; min-height: 24px; }
#news-filter-bar span { color: #00bcd4; }
#clear-filter { cursor:pointer; color:#f44336; padding: 2px 6px; background:transparent;
                border: 1px solid #333; border-radius: 3px; font-size: 10px; font-family: inherit; }
/* Correlation grid */
.corr-grid { display: grid; font-size: 10px; }
.corr-cell { padding: 3px; text-align: center; }
.corr-header { color: #555; font-size: 9px; }

/* ── Sectors tab ──────────────────────────────────────────────────────── */
.sector-row { margin-bottom: 16px; }
.sector-header { display: flex; align-items: center; gap: 10px; margin-bottom: 5px; }
.sector-name { width: 105px; color: #e0e0e0; font-weight: bold; flex-shrink: 0; }
.sector-score { width: 55px; font-weight: bold; flex-shrink: 0; }
.bar-wrap { flex: 1; background: #1a1a1a; border-radius: 3px; height: 12px; }
.bar-fill { height: 12px; border-radius: 3px; transition: width 0.4s; }
.sector-headlines { margin-left: 115px; margin-top: 2px; }
.sector-hl { color: #444; font-size: 11px; margin-bottom: 3px; line-height: 1.4; }
.sector-hl .sym { margin-right: 5px; }

/* ── Watchlist tab ────────────────────────────────────────────────────── */
#watch-table { width: 100%; border-collapse: collapse; }
#watch-table th { color: #444; text-align: left; padding: 5px 8px; border-bottom: 1px solid #1e1e1e; font-weight: normal; font-size: 11px; }
#watch-table td { padding: 7px 8px; border-bottom: 1px solid #161616; }
.ticker-col { color: #00bcd4; font-weight: bold; width: 65px; }
.stars { letter-spacing: -2px; }

/* ── Chart tab ────────────────────────────────────────────────────────── */
#chart-wrap { display: flex; flex-direction: column; gap: 28px; }
.chart-section h3 { color: #444; font-size: 11px; margin-bottom: 12px; font-weight: normal; letter-spacing: .05em; text-transform: uppercase; }
.bar-chart { display: flex; align-items: flex-end; gap: 6px; height: 140px; flex-wrap: wrap; }
.bar-col { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.bar-rect { width: 42px; border-radius: 3px 3px 0 0; transition: height 0.4s; min-height: 2px; }
.bar-label { color: #444; font-size: 10px; text-align: center; }
.bar-score { font-size: 11px; font-weight: bold; }
.hbar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }
.hbar-label { width: 46px; color: #00bcd4; font-weight: bold; font-size: 12px; flex-shrink: 0; }
.hbar-bg { flex: 1; background: #1a1a1a; border-radius: 3px; height: 11px; }
.hbar-fill { height: 11px; border-radius: 3px; transition: width 0.4s; }
.hbar-score { width: 28px; font-size: 11px; font-weight: bold; text-align: right; }

/* ── Bottom nav (mobile only, hidden on desktop) ──────────────────────── */
#bottom-nav { display: none; }

/* ════════════════════════════════════════════════════════════════════════
   MOBILE  (≤ 767 px)
   ════════════════════════════════════════════════════════════════════════ */
@media (max-width: 767px) {
  body { font-size: 14px; padding-bottom: calc(56px + env(safe-area-inset-bottom)); }

  /* Top bar: hide desktop tabs, shrink */
  #topbar { padding: 8px 12px; }
  .dtab { display: none; }
  #topbar .brand { font-size: 15px; margin-right: 0; }
  #status { font-size: 11px; max-width: 180px; }

  /* Main padding */
  #main { padding: 8px; }

  /* ── News: stack list above detail ── */
  #news-wrap { flex-direction: column; gap: 0; }
  #news-list-col { order: 1; }
  #news-detail {
    order: 2; margin-top: 10px; position: static;
    min-width: 0; display: block;
  }

  /* Bigger tap rows */
  #news-table td { padding: 11px 6px; font-size: 13px; }
  #news-table th { padding: 6px 6px; }

  /* Hide source & sector columns — keep age + signal + theme + headline */
  .col-src, th.col-src { display: none; }
  .col-sect, th.col-sect { display: none; }

  /* Sectors: un-indent headlines */
  .sector-headlines { margin-left: 0; margin-top: 6px; }
  .sector-hl { font-size: 12px; }
  .sector-name { width: 90px; font-size: 13px; }
  .bar-wrap { height: 10px; }
  .bar-fill { height: 10px; }

  /* Watchlist: hide Name column, keep Ticker / Sector / Conv / Stars */
  .watch-name, th.watch-name { display: none; }
  #watch-table td, #watch-table th { padding: 10px 6px; font-size: 13px; }

  /* Chart: smaller bars, scrollable */
  .bar-chart { height: 110px; overflow-x: auto; flex-wrap: nowrap; padding-bottom: 4px; }
  .bar-rect { width: 36px; }
  .bar-label { font-size: 9px; }

  /* Bottom nav */
  #bottom-nav {
    display: flex; position: fixed; bottom: 0; left: 0; right: 0;
    height: calc(56px + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
    background: #111; border-top: 1px solid #222;
    z-index: 200;
  }
  .bnav-btn {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    gap: 3px; cursor: pointer; color: #444;
    font-size: 10px; padding: 6px 0;
    border: none; background: transparent;
    -webkit-tap-highlight-color: transparent;
  }
  .bnav-btn.active { color: #00bcd4; }
  .bnav-icon { font-size: 18px; line-height: 1; }
}
</style>
</head>
<body>

<!-- ── Top bar (desktop tabs) ── -->
<div id="topbar">
  <span class="brand">TradeBot</span>
  <span class="dtab active" data-tab="news"     onclick="switchTab('news')">[1] News</span>
  <span class="dtab"        data-tab="sectors"  onclick="switchTab('sectors')">[2] Sectors</span>
  <span class="dtab"        data-tab="mkt"      onclick="switchTab('mkt')">[3] Mkt</span>
  <span class="dtab"        data-tab="watchlist" onclick="switchTab('watchlist')">[4] Watchlist</span>
  <span class="dtab"        data-tab="chart"    onclick="switchTab('chart')">[5] Chart</span>
  <span id="status">loading…</span>
</div>

<!-- ── Panels ── -->
<div id="main">

  <!-- News -->
  <div class="panel active" id="panel-news">
    <div id="news-wrap">
      <div id="news-list-col">
        <table id="news-table">
          <thead><tr>
            <th class="col-age" onclick="sortNews('age')" style="cursor:pointer" title="Sort by age">Age</th>
            <th class="col-src" onclick="sortNews('source')" style="cursor:pointer" title="Sort by source — click a cell to filter">Source</th>
            <th onclick="sortNews('signal')" style="cursor:pointer" title="Sort by signal — click a cell to filter">Signal</th>
            <th class="col-sect" onclick="sortNews('sector')" style="cursor:pointer" title="Sort by sector — click a cell to filter">Sector</th>
            <th class="col-theme" onclick="sortNews('theme')" style="cursor:pointer" title="Sort by theme — click a cell to filter">Theme</th>
            <th>Headline</th>
          </tr></thead>
          <tbody id="news-body">
            <tr><td colspan="6" class="loading-msg">Fetching news feeds…</td></tr>
          </tbody>
        </table>
      </div>
      <div id="news-detail">
        <div style="color:#444;padding:16px 0">Tap a headline to preview</div>
      </div>
    </div>
  </div>

  <!-- Sectors -->
  <div class="panel" id="panel-sectors">
    <div id="sectors-body"><div class="loading-msg">Loading…</div></div>
  </div>

  <!-- Mkt -->
  <div class="panel" id="panel-mkt">
    <div id="mkt-body"><div class="loading-msg">Loading market data…</div></div>
  </div>

  <!-- Watchlist -->
  <div class="panel" id="panel-watchlist">
    <table id="watch-table">
      <thead><tr>
        <th class="ticker-col">Ticker</th>
        <th class="watch-name">Name</th>
        <th>Sector</th>
        <th>Conv.</th>
        <th>Signal</th>
      </tr></thead>
      <tbody id="watch-body">
        <tr><td colspan="5" class="loading-msg">Loading…</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Chart -->
  <div class="panel" id="panel-chart">
    <div id="chart-body"><div class="loading-msg">Loading…</div></div>
  </div>

</div>

<!-- ── Bottom nav (mobile only) ── -->
<nav id="bottom-nav">
  <button class="bnav-btn active" data-tab="news"      onclick="switchTab('news')">
    <span class="bnav-icon">📰</span>News
  </button>
  <button class="bnav-btn"       data-tab="sectors"   onclick="switchTab('sectors')">
    <span class="bnav-icon">🗂️</span>Sectors
  </button>
  <button class="bnav-btn"       data-tab="mkt"       onclick="switchTab('mkt')">
    <span class="bnav-icon">📈</span>Mkt
  </button>
  <button class="bnav-btn"       data-tab="watchlist" onclick="switchTab('watchlist')">
    <span class="bnav-icon">👁️</span>Watch
  </button>
  <button class="bnav-btn"       data-tab="chart"     onclick="switchTab('chart')">
    <span class="bnav-icon">📊</span>Chart
  </button>
</nav>

<script>
'use strict';
let appData = null;
let selectedIdx = 0;
let newsSort = {col: 'age', asc: true};
let newsFilter = {col: null, val: null};
let mktSort = {col: 'category', asc: true};
let polySort = {col: 'volume', asc: false};

/* ── Tab switching (syncs desktop tabs + mobile bottom nav) ── */
function switchTab(name) {
  document.querySelectorAll('.dtab, .bnav-btn').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === name);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
}

/* ── Helpers ── */
function scoreClass(s) { return s >= 60 ? 'c-pos' : s <= 40 ? 'c-neg' : 'c-neu'; }
function scoreBg(s)    { return s >= 60 ? 'bg-pos' : s <= 40 ? 'bg-neg' : 'bg-neu'; }
function sigClass(s)   { return 'sig-' + s; }
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ── News ── */
// col index → field mapping: 0=age, 1=source, 2=signal, 3=sector, 4=theme, 5=headline
const NEWS_COLS = ['age','source','signal','sector','theme','title'];
const FILTERABLE = new Set([1,2,3,4]); // source, signal, sector, theme

function ageSecs(age) {
  if (!age || age === '?') return 999999;
  const n = parseInt(age);
  if (age.endsWith('s')) return n;
  if (age.endsWith('m')) return n * 60;
  if (age.endsWith('h')) return n * 3600;
  if (age.endsWith('d')) return n * 86400;
  return 999999;
}

function renderNews(news) {
  // Apply filter
  let filtered = news;
  if (newsFilter.col && newsFilter.val) {
    filtered = news.filter(a => String(a[newsFilter.col]||'').toLowerCase() === newsFilter.val.toLowerCase());
  }
  // Apply sort
  const col = newsSort.col;
  filtered = [...filtered].sort((a, b) => {
    let va = a[col] ?? '', vb = b[col] ?? '';
    if (col === 'age') { va = ageSecs(String(va)); vb = ageSecs(String(vb)); }
    if (col === 'sentiment') { va = Number(va); vb = Number(vb); }
    if (va < vb) return newsSort.asc ? -1 : 1;
    if (va > vb) return newsSort.asc ? 1 : -1;
    return 0;
  });

  // Update filter bar
  let filterBar = document.getElementById('news-filter-bar');
  if (!filterBar) {
    filterBar = document.createElement('div');
    filterBar.id = 'news-filter-bar';
    const listCol = document.getElementById('news-list-col');
    listCol.insertBefore(filterBar, listCol.firstChild);
  }
  if (newsFilter.col) {
    filterBar.innerHTML = 'Filtered: <span>' + newsFilter.col + ' = ' + esc(newsFilter.val) + '</span>'
      + ' (' + filtered.length + ') <button id="clear-filter" onclick="clearFilter()">✕ clear</button>';
  } else {
    filterBar.innerHTML = filtered.length + ' articles &nbsp;·&nbsp; <span style="color:#555">click signal/theme/sector cell to filter</span>';
  }

  const themeColor = {geopolitics:'#e74c3c',macro:'#f1c40f',crypto:'#1abc9c',markets:'#ecf0f1'};
  const tbody = document.getElementById('news-body');
  tbody.innerHTML = '';

  filtered.forEach((a, i) => {
    const tr = document.createElement('tr');
    if (i === selectedIdx) tr.classList.add('selected');
    const trumpColor = a.is_trump ? 'color:#c678dd;font-weight:bold;' : '';
    const tColor = themeColor[a.theme] || '#ecf0f1';

    // Build cells manually (no inline onclick — use tr.onclick with col index)
    const cells = [
      {cls: 'col-age',  txt: a.age,                            style: ''},
      {cls: 'col-src',  txt: a.source,                         style: 'cursor:pointer'},
      {cls: sigClass(a.signal), txt: a.signal,                 style: 'cursor:pointer'},
      {cls: 'col-sect', txt: a.sector,                         style: 'cursor:pointer'},
      {cls: 'col-theme',txt: a.theme||'markets',               style: 'color:'+tColor+';font-weight:bold;cursor:pointer'},
      {cls: '',         txt: a.title,                          style: trumpColor},
    ];
    cells.forEach(c => {
      const td = document.createElement('td');
      if (c.cls) td.className = c.cls;
      if (c.style) td.style.cssText = c.style;
      td.textContent = c.txt;
      tr.appendChild(td);
    });

    tr.onclick = (e) => {
      const td = e.target.closest('td');
      if (!td) return;
      const colIdx = Array.from(tr.cells).indexOf(td);
      if (FILTERABLE.has(colIdx)) {
        const field = NEWS_COLS[colIdx];
        const val   = String(a[field] || '');
        newsFilter = {col: field, val};
        selectedIdx = 0;
        renderNews(appData.news);
        return;
      }
      // Headline or age — open detail
      selectedIdx = i;
      document.querySelectorAll('#news-table tr').forEach((r, ri) =>
        r.classList.toggle('selected', ri === i + 1));
      renderDetail(a);
      setTimeout(() => document.getElementById('news-detail').scrollIntoView({behavior:'smooth', block:'nearest'}), 50);
    };
    tbody.appendChild(tr);
  });

  if (filtered.length > 0) renderDetail(filtered[Math.min(selectedIdx, filtered.length - 1)]);
}

function sortNews(col) {
  if (newsSort.col === col) newsSort.asc = !newsSort.asc;
  else { newsSort.col = col; newsSort.asc = true; }
  if (appData) renderNews(appData.news);
}

function clearFilter() {
  newsFilter = {col: null, val: null};
  selectedIdx = 0;
  if (appData) renderNews(appData.news);
}

function clearFilter() {
  newsFilter = {col: null, val: null};
  selectedIdx = 0;
  if (appData) renderNews(appData.news);
}

function renderDetail(a) {
  const etfs = (appData.etfs_by_sector[a.sector] || []).slice(0, 4);
  const barPct = a.sentiment + '%';
  const barCls = scoreBg(a.sentiment);
  const scoreC = scoreClass(a.sentiment);
  const etfHtml = etfs.length
    ? '<div class="etf-list"><h4>Related ETFs</h4>' +
      etfs.map(([t,n]) =>
        '<div class="etf-row"><span class="etf-ticker">' + t +
        '</span><span class="etf-name">' + esc(n) + '</span></div>'
      ).join('') + '</div>'
    : '';
  document.getElementById('news-detail').innerHTML =
    '<h3>' + esc(a.title) + '</h3>' +
    '<div class="detail-row"><span class="detail-label">Source</span><span class="detail-val">' + esc(a.source) + '</span></div>' +
    '<div class="detail-row"><span class="detail-label">Sector</span><span class="detail-val" style="color:#00bcd4">' + esc(a.sector) + '</span></div>' +
    '<div class="detail-row"><span class="detail-label">Signal</span><span class="detail-val ' + sigClass(a.signal) + '">' + a.signal.toUpperCase() + '</span></div>' +
    '<div class="detail-row"><span class="detail-label">Age</span><span class="detail-val">' + a.age + ' ago</span></div>' +
    '<div class="detail-row"><span class="detail-label">Sentiment</span><span class="detail-val ' + scoreC + '">' + a.sentiment + '/100</span></div>' +
    '<div class="sent-bar-bg"><div class="sent-bar-fill ' + barCls + '" style="width:' + barPct + '"></div></div>' +
    etfHtml;
}

/* ── Sectors ── */
function renderSectors(sector_scores, news) {
  let html = '';
  for (const [sector, score] of Object.entries(sector_scores)) {
    const cls = scoreClass(score), bgCls = scoreBg(score);
    const hls = news.filter(a => a.sector === sector).slice(0, 2).map(a => {
      const sym = a.signal==='bullish' ? '+' : a.signal==='bearish' ? '−' : '·';
      return '<div class="sector-hl"><span class="sym ' + sigClass(a.signal) + '">' + sym +
             '</span>' + esc(a.title) + '</div>';
    }).join('');
    html +=
      '<div class="sector-row">' +
        '<div class="sector-header">' +
          '<span class="sector-name">' + sector + '</span>' +
          '<span class="sector-score ' + cls + '">' + score + '/100</span>' +
          '<div class="bar-wrap"><div class="bar-fill ' + bgCls + '" style="width:' + score + '%"></div></div>' +
        '</div>' +
        '<div class="sector-headlines">' + hls + '</div>' +
      '</div>';
  }
  document.getElementById('sectors-body').innerHTML = html || '<div class="loading-msg">No data</div>';
}

/* ── Watchlist ── */
function renderWatchlist(recs) {
  const rows = recs.map(r => {
    const cls = scoreClass(r.sentiment);
    const filled = Math.round(r.score);
    const stars = '★'.repeat(filled) + '☆'.repeat(5 - filled);
    return '<tr>' +
      '<td class="ticker-col">' + r.ticker + '</td>' +
      '<td class="watch-name">' + esc(r.name) + '</td>' +
      '<td style="color:#666;font-size:12px">' + r.sector + '</td>' +
      '<td class="' + cls + '" style="font-weight:bold">' + r.score.toFixed(1) + '</td>' +
      '<td class="stars ' + cls + '">' + stars + '</td>' +
    '</tr>';
  }).join('');
  document.getElementById('watch-body').innerHTML =
    rows || '<tr><td colspan="5" class="loading-msg">No data</td></tr>';
}

/* ── Mkt ── */
function renderMkt(data) {
  const mkt = data.mkt_data || [];
  const poly = data.polymarket || [];

  // Sort market data
  const mktSorted = [...mkt].sort((a, b) => {
    let va = a[mktSort.col] ?? '', vb = b[mktSort.col] ?? '';
    if (typeof va === 'number' || mktSort.col === 'chg_pct') { va = va??-9999; vb = vb??-9999; }
    if (va < vb) return mktSort.asc ? -1 : 1;
    if (va > vb) return mktSort.asc ? 1 : -1;
    return 0;
  });

  // Build mkt table
  const mktRows = mktSorted.map(r => {
    const price = r.price != null ? r.price.toLocaleString(undefined, {maximumFractionDigits:2}) : '—';
    const chgVal = r.chg_pct;
    const chgStr = chgVal != null ? (chgVal >= 0 ? '+' : '') + chgVal.toFixed(2) + '%' : '—';
    const chgCls = chgVal == null ? 'chg-neu' : chgVal > 0 ? 'chg-pos' : chgVal < 0 ? 'chg-neg' : 'chg-neu';
    const spark = buildSparkline(r.closes || []);
    return '<tr>' +
      '<td style="color:#00bcd4;font-weight:bold">' + esc(r.symbol) + '</td>' +
      '<td style="color:#666;font-size:11px">' + esc(r.name) + '</td>' +
      '<td class="cat-label">' + r.category + '</td>' +
      '<td class="mkt-price">' + price + '</td>' +
      '<td class="' + chgCls + '">' + chgStr + '</td>' +
      '<td>' + spark + '</td>' +
    '</tr>';
  }).join('');

  const mktUpdated = data.mkt_updated ? ' · Updated ' + data.mkt_updated : '';
  const mktHtml =
    '<div class="mkt-section">' +
      '<h3>Live Markets' + mktUpdated + '</h3>' +
      '<div style="overflow-x:auto">' +
      '<table id="mkt-table"><thead><tr>' +
        '<th onclick="sortMkt(\'symbol\')">Symbol ⇅</th>' +
        '<th onclick="sortMkt(\'name\')">Name ⇅</th>' +
        '<th onclick="sortMkt(\'category\')">Cat ⇅</th>' +
        '<th onclick="sortMkt(\'price\')">Price ⇅</th>' +
        '<th onclick="sortMkt(\'chg_pct\')">Day% ⇅</th>' +
        '<th>5D</th>' +
      '</tr></thead><tbody>' + mktRows + '</tbody></table></div></div>';

  // Sort polymarket
  const polySorted = [...poly].sort((a, b) => {
    let va = a[polySort.col] ?? '', vb = b[polySort.col] ?? '';
    if (va < vb) return polySort.asc ? -1 : 1;
    if (va > vb) return polySort.asc ? 1 : -1;
    return 0;
  });

  const polyRows = polySorted.map(r => {
    const probCls = r.prob >= 60 ? 'c-pos' : r.prob <= 40 ? 'c-neg' : 'c-neu';
    const chgStr = r.chg_1h != null ? (r.chg_1h >= 0 ? '+' : '') + r.chg_1h.toFixed(1) + '%' : '—';
    const chgCls = r.chg_1h == null ? 'chg-neu' : r.chg_1h > 0 ? 'chg-pos' : r.chg_1h < 0 ? 'chg-neg' : 'chg-neu';
    const vol = r.volume >= 1000000 ? '$' + (r.volume/1000000).toFixed(1) + 'M'
               : r.volume >= 1000 ? '$' + (r.volume/1000).toFixed(0) + 'K' : '$' + r.volume;
    return '<tr>' +
      '<td style="color:#c8c8c8">' + esc(r.question) + '</td>' +
      '<td class="' + probCls + '" style="font-weight:bold;white-space:nowrap">' + r.prob.toFixed(1) + '% ' + esc(r.outcome||'Yes') + '</td>' +
      '<td class="' + chgCls + '">' + chgStr + '</td>' +
      '<td style="color:#555">' + vol + '</td>' +
      '<td style="color:#444;font-size:10px">' + (r.end_date||'—') + '</td>' +
    '</tr>';
  }).join('') || '<tr><td colspan="5" class="loading-msg">No Polymarket data</td></tr>';

  const polyHtml =
    '<div class="mkt-section">' +
      '<h3>Polymarket Prediction Markets (finance/macro)</h3>' +
      '<div style="overflow-x:auto">' +
      '<table id="poly-table"><thead><tr>' +
        '<th onclick="sortPoly(\'question\')">Question ⇅</th>' +
        '<th onclick="sortPoly(\'prob\')">Prob ⇅</th>' +
        '<th onclick="sortPoly(\'chg_1h\')">1h Chg ⇅</th>' +
        '<th onclick="sortPoly(\'volume\')">Volume ⇅</th>' +
        '<th onclick="sortPoly(\'end_date\')">Closes ⇅</th>' +
      '</tr></thead><tbody>' + polyRows + '</tbody></table></div></div>';

  document.getElementById('mkt-body').innerHTML = mktHtml + polyHtml;
}

function buildSparkline(closes) {
  if (!closes || closes.length < 2) return '<span style="color:#333">—</span>';
  const min = Math.min(...closes), max = Math.max(...closes);
  const range = max - min || 1;
  const first = closes[0], last = closes[closes.length-1];
  const up = last >= first;
  return '<div class="sparkline">' +
    closes.map(c => {
      const h = Math.round(((c - min) / range) * 16) + 2;
      return '<div class="spark-bar" style="height:' + h + 'px;background:' + (up ? '#4caf50' : '#f44336') + '"></div>';
    }).join('') +
  '</div>';
}

function sortMkt(col) {
  if (mktSort.col === col) mktSort.asc = !mktSort.asc;
  else { mktSort.col = col; mktSort.asc = true; }
  if (appData) renderMkt(appData);
}

function sortPoly(col) {
  if (polySort.col === col) polySort.asc = !polySort.asc;
  else { polySort.col = col; polySort.asc = false; }
  if (appData) renderMkt(appData);
}

/* ── Chart ── */
function renderChart(sector_scores, recs, correlation) {
  const items = Object.entries(sector_scores);
  const maxH = 120;

  const barCols = items.map(([sector, score]) => {
    const h = Math.round((score / 100) * maxH);
    const bgCls = scoreBg(score), cls = scoreClass(score);
    const abbr = sector.length > 7 ? sector.slice(0, 7) : sector;
    return '<div class="bar-col">' +
      '<span class="bar-score ' + cls + '">' + score + '</span>' +
      '<div class="bar-rect ' + bgCls + '" style="height:' + h + 'px"></div>' +
      '<span class="bar-label">' + abbr + '</span>' +
    '</div>';
  }).join('');

  const horizBars = recs.slice(0, 10).map(r => {
    const pct = (r.score / 5 * 100) + '%';
    const bgCls = scoreBg(r.sentiment), cls = scoreClass(r.sentiment);
    return '<div class="hbar-row">' +
      '<span class="hbar-label">' + r.ticker + '</span>' +
      '<div class="hbar-bg"><div class="hbar-fill ' + bgCls + '" style="width:' + pct + '"></div></div>' +
      '<span class="hbar-score ' + cls + '">' + r.score.toFixed(1) + '</span>' +
    '</div>';
  }).join('');

  // Correlation grid
  let corrHtml = '';
  if (correlation && correlation.sectors && correlation.sectors.length > 1) {
    const secs = correlation.sectors;
    const mat  = correlation.matrix;
    const abbr = s => s.length > 6 ? s.slice(0,6) : s;
    const corrColor = v => {
      if (v >= 0.5)  return '#1b5e20'; // strong positive — dark green
      if (v >= 0.2)  return '#388e3c'; // mild positive — green
      if (v >= -0.2) return '#f57f17'; // neutral — amber/yellow
      if (v >= -0.5) return '#c62828'; // mild negative — red
      return '#b71c1c';                // strong negative — dark red
    };
    const n = secs.length;
    let rows = '<div class="corr-grid" style="grid-template-columns: repeat(' + (n+1) + ', 1fr); gap:1px;">';
    // Header row
    rows += '<div class="corr-cell corr-header"></div>';
    secs.forEach(s => rows += '<div class="corr-cell corr-header" title="' + s + '">' + abbr(s) + '</div>');
    // Data rows
    secs.forEach(s1 => {
      rows += '<div class="corr-cell corr-header" title="' + s1 + '">' + abbr(s1) + '</div>';
      secs.forEach(s2 => {
        const v = mat[s1][s2];
        const bg = corrColor(v);
        const txt = s1 === s2 ? '1.0' : v.toFixed(2);
        rows += '<div class="corr-cell" style="background:' + bg + ';color:#fff;font-size:9px" title="' + s1 + ' vs ' + s2 + '">' + txt + '</div>';
      });
    });
    rows += '</div>';
    corrHtml = '<div class="chart-section"><h3>Sector Correlation <span style="color:#444;font-size:10px">(green=positive · yellow=neutral · red=negative)</span></h3>' + rows + '</div>';
  } else {
    corrHtml = '<div class="chart-section"><h3>Sector Correlation</h3><div class="loading-msg" style="font-size:11px">Accumulating snapshots — correlation appears after 2+ data points (every 30 min)</div></div>';
  }

  document.getElementById('chart-body').innerHTML =
    '<div id="chart-wrap">' +
      '<div class="chart-section"><h3>Sentiment by sector</h3>' +
        '<div class="bar-chart">' + barCols + '</div></div>' +
      '<div class="chart-section"><h3>ETF conviction</h3>' + horizBars + '</div>' +
      corrHtml +
    '</div>';
}

/* ── Data fetch & auto-refresh ── */
async function loadData() {
  try {
    const data = await fetch('/api/data').then(r => r.json());
    appData = data;
    document.getElementById('status').textContent = data.loading
      ? 'Fetching feeds…'
      : 'Updated ' + data.last_update + '  ·  ' + data.news.length + ' articles';
    renderNews(data.news);
    renderSectors(data.sector_scores, data.news);
    renderMkt(data);
    renderWatchlist(data.etf_recs);
    renderChart(data.sector_scores, data.etf_recs, data.correlation || {});
  } catch(e) {
    document.getElementById('status').textContent = 'Network error';
  }
}

loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
"""

# ─── HTTP SERVER ─────────────────────────────────────────────────────────────

_state = {
    "news": [],
    "loading": True,
    "error": None,
    "last_update": None,
    "lock": threading.Lock(),
    "mkt_data":    [],
    "mkt_loading": False,
    "mkt_updated": None,
    "polymarket":  [],
    "history":     _load_history_web(),
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress access logs

    def do_GET(self):
        if self.path == "/api/data":
            self._serve_api()
        else:
            self._serve_html()

    def _serve_html(self):
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_api(self):
        with _state["lock"]:
            news = list(_state["news"])
            loading = _state["loading"]
            last_update = _state["last_update"]
            error = _state["error"]
            mkt_data    = list(_state["mkt_data"])
            polymarket  = list(_state["polymarket"])
            history     = list(_state["history"])

        sector_scores = compute_sector_scores(news)
        etf_recs = compute_etf_recommendations(sector_scores)
        etfs_by_sector = {s: SECTOR_ETFS.get(s, []) for s in SECTOR_KEYWORDS}

        payload = json.dumps({
            "loading": loading,
            "error": error,
            "last_update": last_update or "",
            "news": news,
            "sector_scores": sector_scores,
            "etf_recs": etf_recs,
            "etfs_by_sector": etfs_by_sector,
            "mkt_data":    mkt_data,
            "mkt_loading": _state["mkt_loading"],
            "mkt_updated": _state["mkt_updated"] or "",
            "polymarket":  polymarket,
            "correlation": compute_correlation(history) if len(history) >= 2 else {},
        }).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

def bg_fetch():
    fetch_all_news(_state)

def auto_refresh():
    while True:
        time.sleep(300)
        with _state["lock"]:
            if not _state["loading"]:
                _state["loading"] = True
        threading.Thread(target=bg_fetch, daemon=True).start()
        threading.Thread(target=fetch_mkt_data, args=(_state,), daemon=True).start()
        threading.Thread(target=fetch_polymarket, args=(_state,), daemon=True).start()

if __name__ == "__main__":
    threading.Thread(target=bg_fetch, daemon=True).start()
    threading.Thread(target=fetch_mkt_data, args=(_state,), daemon=True).start()
    threading.Thread(target=fetch_polymarket, args=(_state,), daemon=True).start()
    threading.Thread(target=auto_refresh, daemon=True).start()

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"sectornews web running on http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
