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

POSITIVE_WORDS = [
    "surge","soar","rise","gain","rally","jump","climb","boost","record","high",
    "growth","profit","beat","strong","bullish","upside","expand","recovery",
    "approve","buy","upgrade","positive","optimism","acceleration","demand",
    "breakthrough","secure","win","deal","partnership","increase","thrive",
]
NEGATIVE_WORDS = [
    "fall","drop","slump","crash","plunge","decline","loss","miss","weak",
    "bearish","risk","warn","collapse","fail","cut","downgrade","uncertainty",
    "concern","threat","tension","conflict","tariff","sanction","ban","restrict",
    "recession","contraction","default","debt","crisis","investigation","probe",
]

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
    pos = sum(1 for w in POSITIVE_WORDS if w in t)
    neg = sum(1 for w in NEGATIVE_WORDS if w in t)
    total = pos + neg
    if total == 0:
        return 50
    return int((pos / total) * 100)

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

def enrich(article):
    text = article["title"]
    article["sentiment"] = score_sentiment(text)
    article["sector"]    = classify_sector(text)
    article["age"]       = format_age(article.get("pub",""))
    article["signal"]    = ("bullish" if article["sentiment"] >= 60
                            else "bearish" if article["sentiment"] <= 40
                            else "neutral")
    return article

def compute_sector_scores(news):
    sector_data = defaultdict(list)
    for a in news:
        sector_data[a["sector"]].append(a["sentiment"])
    scores = {}
    for sector in SECTOR_KEYWORDS:
        vals = sector_data.get(sector, [])
        scores[sector] = int(sum(vals) / len(vals)) if vals else 50
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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>sectornews</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d0d0d; color: #c8c8c8; font-family: 'Menlo','Monaco','Consolas',monospace; font-size: 13px; }
  #topbar { background: #111; border-bottom: 1px solid #222; display: flex; align-items: center; padding: 6px 12px; gap: 0; position: sticky; top: 0; z-index: 10; }
  #topbar .brand { color: #00bcd4; font-weight: bold; margin-right: 16px; }
  .tab { padding: 4px 12px; cursor: pointer; color: #555; border-radius: 3px; }
  .tab.active { background: #00bcd4; color: #000; font-weight: bold; }
  .tab:hover:not(.active) { color: #aaa; }
  #status { margin-left: auto; color: #444; font-size: 11px; }
  #main { padding: 12px; }
  .panel { display: none; }
  .panel.active { display: block; }

  /* News tab */
  #news-table { width: 100%; border-collapse: collapse; }
  #news-table th { color: #555; text-align: left; padding: 4px 8px; border-bottom: 1px solid #222; font-weight: normal; }
  #news-table td { padding: 5px 8px; border-bottom: 1px solid #181818; vertical-align: top; cursor: pointer; }
  #news-table tr:hover td { background: #161f22; }
  #news-table tr.selected td { background: #0a2a2f; }
  .sig-bullish { color: #4caf50; font-weight: bold; }
  .sig-bearish { color: #f44336; font-weight: bold; }
  .sig-neutral { color: #ffb300; font-weight: bold; }
  .col-age { color: #444; width: 50px; }
  .col-src { color: #00bcd4; width: 100px; }
  .col-sect { width: 110px; }
  .col-head { max-width: 500px; }
  #news-wrap { display: flex; gap: 12px; }
  #news-list-col { flex: 2; }
  #news-detail { flex: 1; background: #111; border: 1px solid #222; border-radius: 4px; padding: 12px; min-height: 300px; }
  #news-detail h3 { color: #e0e0e0; margin-bottom: 12px; font-size: 13px; line-height: 1.5; font-weight: normal; }
  .detail-row { display: flex; margin-bottom: 6px; }
  .detail-label { color: #555; width: 80px; flex-shrink: 0; }
  .detail-val { color: #c8c8c8; }
  .sent-bar-wrap { margin: 10px 0 4px; }
  .sent-bar-bg { background: #222; border-radius: 3px; height: 10px; width: 180px; }
  .sent-bar-fill { height: 10px; border-radius: 3px; }
  .etf-list { margin-top: 10px; }
  .etf-list h4 { color: #555; margin-bottom: 6px; font-weight: normal; font-size: 11px; }
  .etf-row { display: flex; gap: 8px; margin-bottom: 4px; }
  .etf-ticker { color: #4caf50; font-weight: bold; width: 50px; }
  .etf-name { color: #555; }

  /* Sectors tab */
  .sector-row { margin-bottom: 14px; }
  .sector-header { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
  .sector-name { width: 110px; color: #e0e0e0; font-weight: bold; }
  .sector-score { width: 60px; font-weight: bold; }
  .bar-wrap { flex: 1; background: #1a1a1a; border-radius: 3px; height: 14px; max-width: 500px; }
  .bar-fill { height: 14px; border-radius: 3px; transition: width 0.4s; }
  .sector-headlines { margin-left: 122px; }
  .sector-hl { color: #444; font-size: 12px; margin-bottom: 2px; }
  .sector-hl .sig-sym { margin-right: 6px; }

  /* Watchlist tab */
  #watch-table { width: 100%; border-collapse: collapse; }
  #watch-table th { color: #555; text-align: left; padding: 4px 8px; border-bottom: 1px solid #222; font-weight: normal; }
  #watch-table td { padding: 6px 8px; border-bottom: 1px solid #181818; }
  .ticker-col { color: #00bcd4; font-weight: bold; width: 70px; }
  .stars { letter-spacing: -1px; }

  /* Chart tab */
  #chart-wrap { display: flex; flex-direction: column; gap: 24px; }
  .chart-section h3 { color: #555; font-size: 11px; margin-bottom: 10px; font-weight: normal; }
  .bar-chart { display: flex; align-items: flex-end; gap: 6px; height: 140px; }
  .bar-col { display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .bar-rect { width: 44px; border-radius: 3px 3px 0 0; transition: height 0.4s; }
  .bar-label { color: #444; font-size: 10px; text-align: center; }
  .bar-score { font-size: 11px; font-weight: bold; }
  .horiz-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
  .horiz-bar-label { width: 50px; color: #00bcd4; font-weight: bold; font-size: 12px; }
  .horiz-bar-bg { flex: 1; background: #1a1a1a; border-radius: 3px; height: 12px; max-width: 400px; }
  .horiz-bar-fill { height: 12px; border-radius: 3px; }
  .horiz-bar-score { width: 30px; font-size: 11px; font-weight: bold; }

  /* colors */
  .c-pos { color: #4caf50; }
  .c-neg { color: #f44336; }
  .c-neu { color: #ffb300; }
  .bg-pos { background: #4caf50; }
  .bg-neg { background: #f44336; }
  .bg-neu { background: #ffb300; }

  .loading-msg { color: #555; padding: 40px; text-align: center; }
</style>
</head>
<body>
<div id="topbar">
  <span class="brand">sectornews</span>
  <span class="tab active" data-tab="news" onclick="switchTab('news')">[1] News</span>
  <span class="tab" data-tab="sectors" onclick="switchTab('sectors')">[2] Sectors</span>
  <span class="tab" data-tab="watchlist" onclick="switchTab('watchlist')">[3] Watchlist</span>
  <span class="tab" data-tab="chart" onclick="switchTab('chart')">[4] Chart</span>
  <span id="status">loading...</span>
</div>
<div id="main">
  <div class="panel active" id="panel-news">
    <div id="news-wrap">
      <div id="news-list-col">
        <table id="news-table">
          <thead><tr>
            <th class="col-age">Age</th>
            <th class="col-src">Source</th>
            <th>Signal</th>
            <th class="col-sect">Sector</th>
            <th>Headline</th>
          </tr></thead>
          <tbody id="news-body"><tr><td colspan="5" class="loading-msg">Fetching news feeds...</td></tr></tbody>
        </table>
      </div>
      <div id="news-detail">
        <div style="color:#444;padding:20px 0">Select a headline to preview</div>
      </div>
    </div>
  </div>
  <div class="panel" id="panel-sectors">
    <div id="sectors-body"><div class="loading-msg">Loading...</div></div>
  </div>
  <div class="panel" id="panel-watchlist">
    <table id="watch-table">
      <thead><tr>
        <th class="ticker-col">Ticker</th>
        <th>Name</th>
        <th>Sector</th>
        <th>Conv.</th>
        <th>Signal</th>
      </tr></thead>
      <tbody id="watch-body"><tr><td colspan="5" class="loading-msg">Loading...</td></tr></tbody>
    </table>
  </div>
  <div class="panel" id="panel-chart">
    <div id="chart-body"><div class="loading-msg">Loading...</div></div>
  </div>
</div>

<script>
let appData = null;
let selectedIdx = 0;

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`[data-tab="${name}"]`).classList.add('active');
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById(`panel-${name}`).classList.add('active');
}

function scoreClass(score) {
  if (score >= 60) return 'c-pos';
  if (score <= 40) return 'c-neg';
  return 'c-neu';
}
function scoreBg(score) {
  if (score >= 60) return 'bg-pos';
  if (score <= 40) return 'bg-neg';
  return 'bg-neu';
}
function sigClass(sig) {
  return `sig-${sig}`;
}

function renderNews(news) {
  const tbody = document.getElementById('news-body');
  tbody.innerHTML = '';
  news.forEach((a, i) => {
    const tr = document.createElement('tr');
    if (i === selectedIdx) tr.classList.add('selected');
    tr.innerHTML = `
      <td class="col-age" style="color:#444">${a.age}</td>
      <td class="col-src" style="color:#00bcd4">${a.source}</td>
      <td class="${sigClass(a.signal)}">${a.signal}</td>
      <td class="col-sect">${a.sector}</td>
      <td class="col-head">${escHtml(a.title)}</td>`;
    tr.onclick = () => { selectedIdx = i; renderNews(news); renderDetail(a); };
    tbody.appendChild(tr);
  });
  if (news.length > 0 && selectedIdx < news.length) {
    renderDetail(news[selectedIdx]);
  }
}

function renderDetail(a) {
  const etfs = appData.etfs_by_sector[a.sector] || [];
  const barPct = a.sentiment + '%';
  const barCls = scoreBg(a.sentiment);
  const scoreC = scoreClass(a.sentiment);
  let etfHtml = '';
  if (etfs.length) {
    etfHtml = `<div class="etf-list"><h4>Related ETFs</h4>` +
      etfs.slice(0,4).map(([t,n]) =>
        `<div class="etf-row"><span class="etf-ticker">${t}</span><span class="etf-name">${n}</span></div>`
      ).join('') + '</div>';
  }
  document.getElementById('news-detail').innerHTML = `
    <h3>${escHtml(a.title)}</h3>
    <div class="detail-row"><span class="detail-label">Source</span><span class="detail-val">${a.source}</span></div>
    <div class="detail-row"><span class="detail-label">Sector</span><span class="detail-val" style="color:#00bcd4">${a.sector}</span></div>
    <div class="detail-row"><span class="detail-label">Signal</span><span class="detail-val ${sigClass(a.signal)}">${a.signal.toUpperCase()}</span></div>
    <div class="detail-row"><span class="detail-label">Age</span><span class="detail-val">${a.age} ago</span></div>
    <div class="sent-bar-wrap">
      <div class="detail-row"><span class="detail-label">Sentiment</span><span class="detail-val ${scoreC}">${a.sentiment}/100</span></div>
      <div class="sent-bar-bg"><div class="sent-bar-fill ${barCls}" style="width:${barPct}"></div></div>
    </div>
    ${etfHtml}`;
}

function renderSectors(sector_scores, news) {
  const div = document.getElementById('sectors-body');
  let html = '';
  for (const [sector, score] of Object.entries(sector_scores)) {
    const cls = scoreClass(score);
    const bgCls = scoreBg(score);
    const pct = score + '%';
    const headlines = news.filter(a => a.sector === sector).slice(0,2);
    const hlHtml = headlines.map(a => {
      const sym = a.signal==='bullish' ? '+' : a.signal==='bearish' ? '-' : '~';
      const symCls = sigClass(a.signal);
      return `<div class="sector-hl"><span class="sig-sym ${symCls}">${sym}</span>${escHtml(a.title)}</div>`;
    }).join('');
    html += `<div class="sector-row">
      <div class="sector-header">
        <span class="sector-name">${sector}</span>
        <span class="sector-score ${cls}">${score}/100</span>
        <div class="bar-wrap"><div class="bar-fill ${bgCls}" style="width:${pct}"></div></div>
      </div>
      <div class="sector-headlines">${hlHtml}</div>
    </div>`;
  }
  div.innerHTML = html || '<div class="loading-msg">No data</div>';
}

function renderWatchlist(recs) {
  const tbody = document.getElementById('watch-body');
  tbody.innerHTML = recs.map(r => {
    const cls = scoreClass(r.sentiment);
    const stars = '★'.repeat(Math.round(r.score)) + '☆'.repeat(5 - Math.round(r.score));
    return `<tr>
      <td class="ticker-col">${r.ticker}</td>
      <td>${r.name}</td>
      <td style="color:#555">${r.sector}</td>
      <td class="${cls}" style="font-weight:bold">${r.score.toFixed(1)}</td>
      <td class="stars ${cls}">${stars}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" class="loading-msg">No data</td></tr>';
}

function renderChart(sector_scores, recs) {
  const div = document.getElementById('chart-body');
  const items = Object.entries(sector_scores);
  const maxH = 120;
  const barCols = items.map(([sector, score]) => {
    const h = Math.round((score / 100) * maxH);
    const bgCls = scoreBg(score);
    const cls = scoreClass(score);
    const abbr = sector.length > 6 ? sector.slice(0,6) : sector;
    return `<div class="bar-col">
      <span class="bar-score ${cls}">${score}</span>
      <div class="bar-rect ${bgCls}" style="height:${h}px"></div>
      <span class="bar-label">${abbr}</span>
    </div>`;
  }).join('');

  const horizBars = recs.slice(0,8).map(r => {
    const pct = (r.score / 5 * 100) + '%';
    const bgCls = scoreBg(r.sentiment);
    const cls = scoreClass(r.sentiment);
    return `<div class="horiz-bar-row">
      <span class="horiz-bar-label">${r.ticker}</span>
      <div class="horiz-bar-bg"><div class="horiz-bar-fill ${bgCls}" style="width:${pct}"></div></div>
      <span class="horiz-bar-score ${cls}">${r.score.toFixed(1)}</span>
    </div>`;
  }).join('');

  div.innerHTML = `
    <div id="chart-wrap">
      <div class="chart-section">
        <h3>Sentiment scores by sector</h3>
        <div class="bar-chart">${barCols}</div>
      </div>
      <div class="chart-section">
        <h3>ETF conviction scores</h3>
        ${horizBars}
      </div>
    </div>`;
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function loadData() {
  try {
    const res = await fetch('/api/data');
    const data = await res.json();
    appData = data;
    const statusEl = document.getElementById('status');
    if (data.loading) {
      statusEl.textContent = 'Fetching feeds...';
    } else {
      statusEl.textContent = `Updated: ${data.last_update}  |  ${data.news.length} articles  |  Sources: Reuters·AP·MarketWatch·CNBC·FT`;
    }
    renderNews(data.news);
    renderSectors(data.sector_scores, data.news);
    renderWatchlist(data.etf_recs);
    renderChart(data.sector_scores, data.etf_recs);
  } catch(e) {
    document.getElementById('status').textContent = 'Error fetching data';
  }
}

loadData();
setInterval(loadData, 30000);  // refresh every 30s
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
