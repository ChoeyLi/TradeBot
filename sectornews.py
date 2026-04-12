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

# ─── SENTIMENT KEYWORDS ───────────────────────────────────────────────────────

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

# ─── APP STATE ────────────────────────────────────────────────────────────────

class AppState:
    def __init__(self):
        self.news           = []
        self.loading        = True
        self.error          = None
        self.tab            = 0          # 0=News 1=Sectors 2=Watchlist 3=Chart
        self.sel            = 0
        self.scroll         = 0
        self.etf_scroll     = 0
        self.last_update    = None
        self.feeds_ok       = 0
        self.feeds_total    = len(RSS_FEEDS)
        self.sector_filter  = None       # set when user clicks a sector name
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

def enrich(article):
    text = article["title"]
    article["sentiment"] = score_sentiment(text)
    article["sector"]    = classify_sector(text)
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
    all_articles.sort(key=lambda x: x.get("pub",""), reverse=True)
    with state.lock:
        state.news        = all_articles[:60]
        state.loading     = False
        state.feeds_ok    = sum(ok_flags)
        state.last_update = datetime.now().strftime("%H:%M:%S")
        state.error       = None if all_articles else "No articles loaded — check your internet connection"

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
    tabs  = ["News","Sectors","Watchlist","Chart"]
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

# ─── TAB 1: NEWS ─────────────────────────────────────────────────────────────

def draw_news(win, state):
    h, w   = win.getmaxyx()
    # Apply sector filter if set
    news   = [a for a in state.news if a.get("sector") == state.sector_filter] \
             if state.sector_filter else state.news
    sel    = state.sel
    scroll = state.scroll
    split  = w * 3 // 5

    # Column headers
    filter_label = f"  [filter: {state.sector_filter}  click sector to clear]" \
                   if state.sector_filter else ""
    safe_addstr(win, 1, 1,
        f"{'AGE':<5}  {'SOURCE':<13}  {'SIGNAL':<8}  {'SECTOR':<13}  HEADLINE",
        curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 1, split - len(filter_label) - 1,
        filter_label, curses.color_pair(C_NEU) | curses.A_BOLD)
    win.hline(2, 0, curses.ACS_HLINE, split - 1)

    content_h = h - 4
    for i, article in enumerate(news[scroll: scroll + content_h]):
        row   = i + 3
        idx   = i + scroll
        is_sel = (idx == sel)

        if row >= h - 1:
            break

        age  = article.get("age","?")[:5]
        src  = article.get("source","")[:12]
        sig  = article.get("signal","neutral")
        sect = article.get("sector","")[:12]
        title= article.get("title","")
        avail= split - 44

        if is_sel:
            win.hline(row, 0, " ", split - 1)
            base = curses.color_pair(C_SEL)
            safe_addstr(win, row, 1,  f"{age:<5}",   base)
            safe_addstr(win, row, 8,  f"{src:<13}",  base)
            safe_addstr(win, row, 22, f"{sig:<8}",   base | curses.A_BOLD)
            safe_addstr(win, row, 31, f"{sect:<13}", base)
            safe_addstr(win, row, 45, title[:avail], base)
        else:
            safe_addstr(win, row, 1,  f"{age:<5}",   curses.color_pair(C_DIM))
            safe_addstr(win, row, 8,  f"{src:<13}",  curses.color_pair(C_ACCENT))
            safe_addstr(win, row, 22, f"{sig:<8}",   signal_color(sig) | curses.A_BOLD)
            safe_addstr(win, row, 31, f"{sect:<13}", curses.color_pair(C_TITLE))
            safe_addstr(win, row, 45, title[:avail], curses.color_pair(C_TITLE))

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
    safe_addstr(win, 1, 20, "scored from live news signals  [click a sector to filter News tab]",
                curses.color_pair(C_DIM))
    safe_addstr(win, 2, 2,
        "Score 0-100  │  ≥60 bullish  │  40-60 neutral  │  ≤40 bearish",
        curses.color_pair(C_DIM))
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

# ─── TAB 3: WATCHLIST ────────────────────────────────────────────────────────

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

# ─── TAB 4: CHART ────────────────────────────────────────────────────────────

def draw_chart(win, state):
    h, w   = win.getmaxyx()
    scores = compute_sector_scores(state.news)
    recs   = compute_etf_recommendations(scores)[:8]
    items  = list(scores.items())

    safe_addstr(win, 1, 2, "SECTOR CHART", curses.color_pair(C_HEADER) | curses.A_BOLD)
    win.hline(2, 0, curses.ACS_HLINE, w)

    # ── Vertical bar chart ──
    chart_h  = min((h - 8) // 2, 12)
    bar_unit = max(3, (w - 20) // max(len(items), 1))
    safe_addstr(win, 3, 2, "Sentiment by sector", curses.color_pair(C_DIM))

    for i, (sector, score) in enumerate(items):
        col   = 2 + i * bar_unit
        bar_h = int((score / 100) * chart_h)
        scol  = score_color(score)
        for r in range(chart_h):
            row = 4 + chart_h - r
            if row < h - 1:
                ch   = "█" if r < bar_h else "░"
                attr = scol if r < bar_h else curses.color_pair(C_BORDER)
                for bc in range(min(bar_unit - 1, 7)):
                    safe_addstr(win, row, col + bc, ch, attr)
        label = sector[:bar_unit-1]
        safe_addstr(win, 4 + chart_h + 1, col, label,       curses.color_pair(C_DIM))
        safe_addstr(win, 4 + chart_h + 2, col, str(score),  score_color(score) | curses.A_BOLD)

    # ── ETF horizontal bars ──
    div_row = 4 + chart_h + 4
    if div_row < h - 3:
        win.hline(div_row, 0, curses.ACS_HLINE, w)
        safe_addstr(win, div_row + 1, 2, "ETF conviction scores", curses.color_pair(C_DIM))
        bar_max = w - 32
        for j, r in enumerate(recs):
            row = div_row + 2 + j
            if row >= h - 1: break
            bar_w = int((r["score"] / 5.0) * bar_max)
            scol  = score_color(r["sentiment"])
            safe_addstr(win, row, 2,  f"{r['ticker']:<6}", curses.color_pair(C_ACCENT) | curses.A_BOLD)
            safe_addstr(win, row, 9,  "█" * bar_w,          scol)
            safe_addstr(win, row, 9 + bar_w, f"░" * (bar_max - bar_w), curses.color_pair(C_BORDER))
            safe_addstr(win, row, 10 + bar_max, f" {r['score']:.1f}", scol | curses.A_BOLD)

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

    def bg_fetch():
        fetch_all_news(state)

    t = threading.Thread(target=bg_fetch, daemon=True)
    t.start()

    last_refresh = time.time()

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        with state.lock:
            draw_topbar(stdscr, state)
            if state.loading:
                msg = "⠿ Fetching news feeds in parallel… please wait"
                safe_addstr(stdscr, h//2,
                    max(0, (w - len(msg)) // 2), msg,
                    curses.color_pair(C_LOADING) | curses.A_BOLD)
            else:
                if   state.tab == 0: draw_news(stdscr, state)
                elif state.tab == 1: draw_sectors(stdscr, state)
                elif state.tab == 2: draw_watchlist(stdscr, state)
                elif state.tab == 3: draw_chart(stdscr, state)
            draw_statusbar(stdscr, state)

        stdscr.refresh()
        key = stdscr.getch()

        if key in (ord('q'), ord('Q')):
            break
        elif key == ord('1'): state.tab = 0
        elif key == ord('2'): state.tab = 1
        elif key == ord('3'): state.tab = 2
        elif key == ord('4'): state.tab = 3

        elif key == curses.KEY_MOUSE:
            try:
                _, mx, my, _, bstate = curses.getmouse()
                if bstate & curses.BUTTON1_CLICKED or bstate & curses.BUTTON1_PRESSED:
                    # ── Top bar tab clicks ──
                    if my == 0:
                        tabs_start = [13, 21, 31, 43]  # approx col positions
                        for i, col in enumerate(tabs_start):
                            if mx >= col and (i == len(tabs_start)-1 or mx < tabs_start[i+1]+2):
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
            elif state.tab == 2:
                state.etf_scroll = max(0, state.etf_scroll - 1)

        elif key == curses.KEY_DOWN:
            if state.tab == 0:
                state.sel = min(max(0, len(state.news)-1), state.sel + 1)
                if state.sel >= state.scroll + (h - 5):
                    state.scroll = state.sel - (h - 6)
            elif state.tab == 2:
                state.etf_scroll += 1

        elif key in (ord('r'), ord('R')):
            if not state.loading:
                state.loading = True
                state.news    = []
                threading.Thread(target=bg_fetch, daemon=True).start()

        # Auto-refresh every 5 minutes
        if time.time() - last_refresh > 300 and not state.loading:
            last_refresh = time.time()
            state.loading = True
            threading.Thread(target=bg_fetch, daemon=True).start()

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    print("\nTradeBot closed. Run again with: python3 sectornews.py\n")
