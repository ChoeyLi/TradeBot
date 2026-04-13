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
    all_articles.sort(key=lambda x: x.get("pub",""), reverse=True)
    with state["lock"]:
        state["news"]        = all_articles[:60]
        state["loading"]     = False
        state["last_update"] = datetime.now().strftime("%H:%M:%S")
        state["error"]       = None if all_articles else "No articles loaded (check internet)"

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
    min-width: 0; display: none; /* shown via JS when row tapped */
  }
  #news-detail.visible { display: block; }

  /* Bigger tap rows */
  #news-table td { padding: 11px 6px; font-size: 13px; }
  #news-table th { padding: 6px 6px; }

  /* Hide source, sector & theme columns — keep age + signal + headline */
  .col-src, th.col-src { display: none; }
  .col-sect, th.col-sect { display: none; }
  .col-theme, th.col-theme { display: none; }

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
  <span class="dtab"        data-tab="watchlist" onclick="switchTab('watchlist')">[3] Watchlist</span>
  <span class="dtab"        data-tab="chart"    onclick="switchTab('chart')">[4] Chart</span>
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
            <th class="col-age">Age</th>
            <th class="col-src">Source</th>
            <th>Signal</th>
            <th class="col-sect">Sector</th>
            <th class="col-theme">Theme</th>
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

/* ── Tab switching (syncs desktop tabs + mobile bottom nav) ── */
function switchTab(name) {
  document.querySelectorAll('.dtab, .bnav-btn').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === name);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  // Hide detail pane when switching away from news on mobile
  if (name !== 'news') {
    document.getElementById('news-detail').classList.remove('visible');
  }
}

/* ── Helpers ── */
function scoreClass(s) { return s >= 60 ? 'c-pos' : s <= 40 ? 'c-neg' : 'c-neu'; }
function scoreBg(s)    { return s >= 60 ? 'bg-pos' : s <= 40 ? 'bg-neg' : 'bg-neu'; }
function sigClass(s)   { return 'sig-' + s; }
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ── News ── */
function renderNews(news) {
  const tbody = document.getElementById('news-body');
  tbody.innerHTML = '';
  news.forEach((a, i) => {
    const tr = document.createElement('tr');
    if (i === selectedIdx) tr.classList.add('selected');
    const themeColor = {geopolitics:'#e74c3c',macro:'#f1c40f',crypto:'#1abc9c',markets:'#ecf0f1'};
    const trumpStyle = a.is_trump ? ' style="color:#c678dd;font-weight:bold"' : '';
    tr.innerHTML =
      '<td class="col-age">' + a.age + '</td>' +
      '<td class="col-src">' + esc(a.source) + '</td>' +
      '<td class="' + sigClass(a.signal) + '">' + a.signal + '</td>' +
      '<td class="col-sect">' + esc(a.sector) + '</td>' +
      '<td class="col-theme" style="color:' + (themeColor[a.theme]||'#ecf0f1') + ';font-weight:bold">' + esc(a.theme||'markets') + '</td>' +
      '<td' + trumpStyle + '>' + esc(a.title) + '</td>';
    tr.onclick = () => {
      selectedIdx = i;
      document.querySelectorAll('#news-table tr').forEach((r,ri) =>
        r.classList.toggle('selected', ri === i + 1));
      renderDetail(a);
      // On mobile, show the detail panel and scroll to it
      const det = document.getElementById('news-detail');
      det.classList.add('visible');
      setTimeout(() => det.scrollIntoView({behavior:'smooth', block:'nearest'}), 50);
    };
    tbody.appendChild(tr);
  });
  if (news.length > 0 && selectedIdx < news.length) {
    renderDetail(news[selectedIdx]);
  }
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

/* ── Chart ── */
function renderChart(sector_scores, recs) {
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

  document.getElementById('chart-body').innerHTML =
    '<div id="chart-wrap">' +
      '<div class="chart-section"><h3>Sentiment by sector</h3>' +
        '<div class="bar-chart">' + barCols + '</div></div>' +
      '<div class="chart-section"><h3>ETF conviction</h3>' + horizBars + '</div>' +
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
    renderWatchlist(data.etf_recs);
    renderChart(data.sector_scores, data.etf_recs);
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
        self.end_headers()
        self.wfile.write(body)

    def _serve_api(self):
        with _state["lock"]:
            news = list(_state["news"])
            loading = _state["loading"]
            last_update = _state["last_update"]
            error = _state["error"]

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

if __name__ == "__main__":
    threading.Thread(target=bg_fetch, daemon=True).start()
    threading.Thread(target=auto_refresh, daemon=True).start()

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"sectornews web running on http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
