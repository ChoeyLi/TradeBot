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
from datetime import datetime, timezone
from collections import defaultdict

# ─── RSS FEEDS (free & public) ───────────────────────────────────────────────

RSS_FEEDS = [
    ("Reuters",      "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters",      "https://feeds.reuters.com/reuters/technologyNews"),
    ("MarketWatch",  "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
    ("AP",           "https://feeds.apnews.com/rss/apf-business"),
    ("AP",           "https://feeds.apnews.com/rss/apf-technology"),
    ("Seeking Alpha","https://seekingalpha.com/feed.xml"),
    ("CNBC",         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("CNBC",         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147"),
    ("FT",           "https://www.ft.com/rss/home"),
    ("Investopedia", "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_headline"),
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

# ─── COLORS ───────────────────────────────────────────────────────────────────

C_HEADER   = 1
C_POS      = 2
C_NEG      = 3
C_NEU      = 4
C_SEL      = 5
C_DIM      = 6
C_TITLE    = 7
C_ACCENT   = 8
C_BAR_POS  = 9
C_BAR_NEG  = 10
C_BAR_NEU  = 11

# ─── APP STATE ────────────────────────────────────────────────────────────────

class AppState:
    def __init__(self):
        self.news        = []
        self.loading     = True
        self.error       = None
        self.tab         = 0          # 0=News 1=Sectors 2=Watchlist 3=Chart
        self.sel         = 0          # selected news row
        self.scroll      = 0          # news scroll offset
        self.etf_scroll  = 0
        self.last_update = None
        self.lock        = threading.Lock()

# ─── RSS FETCH ────────────────────────────────────────────────────────────────

def fetch_feed(source, url, timeout=8):
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"sectornews/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
            if title:
                articles.append({"source": source, "title": title, "pub": pub, "url": url})
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
        if vals:
            scores[sector] = int(sum(vals) / len(vals))
        else:
            scores[sector] = 50
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
    all_articles = []
    for source, url in RSS_FEEDS:
        articles = fetch_feed(source, url)
        for a in articles:
            all_articles.append(enrich(a))
    all_articles.sort(key=lambda x: x.get("pub",""), reverse=True)
    with state.lock:
        state.news        = all_articles[:60]
        state.loading     = False
        state.last_update = datetime.now().strftime("%H:%M:%S")
        state.error       = None if all_articles else "No articles loaded (check internet)"

# ─── DRAWING ─────────────────────────────────────────────────────────────────

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_HEADER,  curses.COLOR_CYAN,    -1)
    curses.init_pair(C_POS,     curses.COLOR_GREEN,   -1)
    curses.init_pair(C_NEG,     curses.COLOR_RED,     -1)
    curses.init_pair(C_NEU,     curses.COLOR_YELLOW,  -1)
    curses.init_pair(C_SEL,     curses.COLOR_BLACK,   curses.COLOR_CYAN)
    curses.init_pair(C_DIM,     8,                    -1)
    curses.init_pair(C_TITLE,   curses.COLOR_WHITE,   -1)
    curses.init_pair(C_ACCENT,  curses.COLOR_CYAN,    -1)
    curses.init_pair(C_BAR_POS, curses.COLOR_GREEN,   -1)
    curses.init_pair(C_BAR_NEG, curses.COLOR_RED,     -1)
    curses.init_pair(C_BAR_NEU, curses.COLOR_YELLOW,  -1)

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
    bar = " sectornews  [1]News  [2]Sectors  [3]Watchlist  [4]Chart "
    tabs = ["News","Sectors","Watchlist","Chart"]
    win.attron(curses.color_pair(C_HEADER) | curses.A_BOLD)
    win.hline(0, 0, " ", w)
    safe_addstr(win, 0, 1, " sectornews ", curses.color_pair(C_HEADER) | curses.A_BOLD)
    win.attroff(curses.color_pair(C_HEADER) | curses.A_BOLD)
    col = 14
    for i, t in enumerate(tabs):
        label = f" [{i+1}]{t} "
        if i == state.tab:
            safe_addstr(win, 0, col, label, curses.color_pair(C_SEL) | curses.A_BOLD)
        else:
            safe_addstr(win, 0, col, label, curses.color_pair(C_DIM))
        col += len(label)
    ts = f" {datetime.now().strftime('%H:%M:%S')} "
    safe_addstr(win, 0, w - len(ts) - 1, ts, curses.color_pair(C_DIM))

def draw_statusbar(win, state):
    h, w = win.getmaxyx()
    if state.loading:
        msg = " Fetching news feeds...  Press Q to quit"
    elif state.error:
        msg = f" {state.error}"
    else:
        upd = state.last_update or "?"
        msg = f" [↑↓]navigate  [Enter]preview  [R]refresh  [Q]quit   Updated: {upd}  Sources: Reuters·AP·MarketWatch·CNBC·FT  (free RSS)"
    win.attron(curses.color_pair(C_DIM))
    win.hline(h-1, 0, " ", w)
    safe_addstr(win, h-1, 0, msg[:w-1], curses.color_pair(C_DIM))
    win.attroff(curses.color_pair(C_DIM))

def signal_color(signal):
    if signal == "bullish": return curses.color_pair(C_POS)
    if signal == "bearish": return curses.color_pair(C_NEG)
    return curses.color_pair(C_NEU)

def score_color(score):
    if score >= 60: return curses.color_pair(C_POS)
    if score <= 40: return curses.color_pair(C_NEG)
    return curses.color_pair(C_NEU)

def draw_news(win, state):
    h, w = win.getmaxyx()
    news  = state.news
    sel   = state.sel
    scroll= state.scroll
    content_h = h - 3
    split = w * 2 // 3

    # Column headers
    safe_addstr(win, 1, 0, f"{'Age':<5} {'Source':<12} {'Signal':<9} {'Sector':<13} {'Headline'}", curses.color_pair(C_DIM) | curses.A_BOLD)
    win.hline(2, 0, curses.ACS_HLINE, split - 1)

    # News list
    for i, article in enumerate(news[scroll: scroll + content_h - 2]):
        row   = i + 3
        idx   = i + scroll
        is_sel= (idx == sel)
        age   = article.get("age","?")[:5]
        src   = article.get("source","")[:11]
        sig   = article.get("signal","neutral")[:8]
        sect  = article.get("sector","")[:12]
        title = article.get("title","")

        if row >= h - 1:
            break

        attr = curses.color_pair(C_SEL) if is_sel else 0
        if is_sel:
            win.hline(row, 0, " ", split - 1)

        safe_addstr(win, row, 0,  f"{age:<5}", attr | curses.color_pair(C_DIM) if not is_sel else curses.color_pair(C_SEL))
        safe_addstr(win, row, 5,  f"{src:<12}", attr | curses.color_pair(C_ACCENT) if not is_sel else curses.color_pair(C_SEL))

        sig_attr = signal_color(sig) if not is_sel else curses.color_pair(C_SEL)
        safe_addstr(win, row, 17, f"{sig:<9}", sig_attr | curses.A_BOLD)

        safe_addstr(win, row, 26, f"{sect:<13}", attr)

        avail = split - 40
        safe_addstr(win, row, 39, title[:avail], attr)

    # Vertical divider
    for r in range(1, h-1):
        try:
            win.addch(r, split, curses.ACS_VLINE, curses.color_pair(C_DIM))
        except curses.error:
            pass

    # Preview panel
    safe_addstr(win, 1, split+2, "News Context", curses.color_pair(C_HEADER) | curses.A_BOLD)
    win.hline(2, split+1, curses.ACS_HLINE, w - split - 2)

    if sel < len(news):
        a    = news[sel]
        title= a.get("title","")
        sig  = a.get("signal","neutral")
        score= a.get("sentiment", 50)
        sect = a.get("sector","")
        src  = a.get("source","")
        age  = a.get("age","?")
        etfs = SECTOR_ETFS.get(sect, [])[:4]

        prow = 3
        pw   = w - split - 3
        # wrap title
        words = title.split()
        line  = ""
        for word in words:
            if len(line) + len(word) + 1 <= pw:
                line += ("" if not line else " ") + word
            else:
                if prow < h - 2:
                    safe_addstr(win, prow, split+2, line, curses.color_pair(C_TITLE))
                prow += 1
                line = word
        if line and prow < h - 2:
            safe_addstr(win, prow, split+2, line, curses.color_pair(C_TITLE))
        prow += 2

        def preview_row(label, val, val_attr=0):
            nonlocal prow
            if prow >= h - 2: return
            safe_addstr(win, prow, split+2, f"{label:<10}", curses.color_pair(C_DIM))
            safe_addstr(win, prow, split+12, val, val_attr or curses.color_pair(C_TITLE))
            prow += 1

        preview_row("Source",  src)
        preview_row("Sector",  sect, curses.color_pair(C_ACCENT))
        preview_row("Signal",  sig.upper(), signal_color(sig) | curses.A_BOLD)
        preview_row("Age",     age + " ago")

        # Sentiment bar
        if prow + 3 < h - 2:
            prow += 1
            safe_addstr(win, prow, split+2, f"Sentiment  {score}/100", score_color(score) | curses.A_BOLD)
            prow += 1
            bar_w = min(pw - 2, 24)
            filled = int((score / 100) * bar_w)
            bar = "█" * filled + "░" * (bar_w - filled)
            safe_addstr(win, prow, split+2, bar, score_color(score))
            prow += 2

        # Related ETFs
        if etfs and prow + 2 < h - 2:
            safe_addstr(win, prow, split+2, "Related ETFs", curses.color_pair(C_DIM))
            prow += 1
            for ticker, name in etfs:
                if prow >= h - 2: break
                safe_addstr(win, prow, split+2, f"  {ticker:<6}", curses.color_pair(C_POS) | curses.A_BOLD)
                safe_addstr(win, prow, split+9, name[:pw-8], curses.color_pair(C_DIM))
                prow += 1

def draw_sectors(win, state):
    h, w = win.getmaxyx()
    news  = state.news
    scores= compute_sector_scores(news)

    safe_addstr(win, 1, 2, "Sector Sentiment  (scored from live news signals)", curses.color_pair(C_HEADER) | curses.A_BOLD)
    safe_addstr(win, 2, 2, "Score 0-100  |  Green >60 bullish  |  Yellow 40-60 neutral  |  Red <40 bearish", curses.color_pair(C_DIM))
    win.hline(3, 0, curses.ACS_HLINE, w)

    row = 4
    bar_max = w - 30
    for sector, score in scores.items():
        if row >= h - 3:
            break
        label = f"{sector:<14}"
        score_str = f"{score:>3}/100"
        bar_w   = int((score / 100) * bar_max)
        bar_fill= "█" * bar_w
        bar_empty="░" * (bar_max - bar_w)
        scol    = score_color(score)

        safe_addstr(win, row, 2, label, curses.color_pair(C_TITLE) | curses.A_BOLD)
        safe_addstr(win, row, 17, score_str, scol | curses.A_BOLD)
        safe_addstr(win, row, 25, bar_fill,  scol)
        safe_addstr(win, row, 25+bar_w, bar_empty, curses.color_pair(C_DIM))

        row += 1
        # Show top 2 headlines for this sector
        sector_news = [a for a in news if a.get("sector") == sector][:2]
        for a in sector_news:
            if row >= h - 3: break
            sig_sym = "+" if a["signal"]=="bullish" else "-" if a["signal"]=="bearish" else "~"
            sig_col = signal_color(a["signal"])
            safe_addstr(win, row, 4, sig_sym, sig_col | curses.A_BOLD)
            safe_addstr(win, row, 6, a["title"][:w-10], curses.color_pair(C_DIM))
            row += 1
        row += 1

def draw_watchlist(win, state):
    h, w = win.getmaxyx()
    news  = state.news
    scores= compute_sector_scores(news)
    recs  = compute_etf_recommendations(scores)

    safe_addstr(win, 1, 2, "ETF Watchlist  (recommended from sector sentiment signals)", curses.color_pair(C_HEADER) | curses.A_BOLD)
    safe_addstr(win, 2, 2, "Conviction 1.0-5.0  |  Based on news signal strength per sector", curses.color_pair(C_DIM))
    win.hline(3, 0, curses.ACS_HLINE, w)
    safe_addstr(win, 4, 2,  f"{'Ticker':<8}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 4, 10, f"{'Name':<38}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 4, 48, f"{'Sector':<14}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 4, 62, f"{'Conv.':<7}", curses.color_pair(C_DIM) | curses.A_BOLD)
    safe_addstr(win, 4, 69, "Signal", curses.color_pair(C_DIM) | curses.A_BOLD)
    win.hline(5, 0, curses.ACS_HLINE, w)

    scroll = state.etf_scroll
    visible= recs[scroll: scroll + h - 8]
    for i, r in enumerate(visible):
        row = 6 + i
        if row >= h - 1: break
        score = r["score"]
        sent  = r["sentiment"]
        scol  = score_color(sent)
        stars = "★" * int(round(score)) + "☆" * (5 - int(round(score)))

        safe_addstr(win, row, 2,  r["ticker"],           curses.color_pair(C_ACCENT) | curses.A_BOLD)
        safe_addstr(win, row, 10, r["name"][:37],        curses.color_pair(C_TITLE))
        safe_addstr(win, row, 48, r["sector"][:13],      curses.color_pair(C_DIM))
        safe_addstr(win, row, 62, f"{score:.1f}",        scol | curses.A_BOLD)
        safe_addstr(win, row, 69, stars,                 scol)

def draw_chart(win, state):
    h, w = win.getmaxyx()
    news   = state.news
    scores = compute_sector_scores(news)

    safe_addstr(win, 1, 2, "Sector Sentiment Chart", curses.color_pair(C_HEADER) | curses.A_BOLD)
    win.hline(2, 0, curses.ACS_HLINE, w)

    chart_h = (h - 5) // 2 - 1
    chart_w = w - 20
    max_val = 100
    items   = list(scores.items())

    # Bar chart
    safe_addstr(win, 3, 2, "Sentiment scores by sector (bar chart)", curses.color_pair(C_DIM))
    bar_unit = chart_w // max(len(items), 1)

    for i, (sector, score) in enumerate(items):
        col = 2 + i * bar_unit
        bar_h = int((score / max_val) * chart_h)
        scol  = score_color(score)
        for r in range(chart_h):
            row = 4 + chart_h - r
            if row < h - 1:
                ch = "█" if r < bar_h else " "
                attr = scol if r < bar_h else curses.color_pair(C_DIM)
                for bc in range(min(bar_unit - 1, 6)):
                    safe_addstr(win, row, col+bc, ch, attr)
        label = sector[:bar_unit-1]
        safe_addstr(win, 4 + chart_h + 1, col, label, curses.color_pair(C_DIM))
        safe_addstr(win, 4 + chart_h + 2, col, str(score), score_color(score) | curses.A_BOLD)

    # Horizontal divider between charts
    div_row = 4 + chart_h + 3
    if div_row < h - 4:
        win.hline(div_row, 0, curses.ACS_HLINE, w)
        safe_addstr(win, div_row+1, 2, "ETF conviction scores", curses.color_pair(C_DIM))
        recs = compute_etf_recommendations(scores)[:8]
        bar_max = w - 30
        for j, r in enumerate(recs):
            row = div_row + 2 + j
            if row >= h - 1: break
            score = r["score"]
            scol  = score_color(r["sentiment"])
            bar_w = int((score / 5.0) * bar_max)
            safe_addstr(win, row, 2,  f"{r['ticker']:<6}", curses.color_pair(C_ACCENT) | curses.A_BOLD)
            safe_addstr(win, row, 9,  "█" * bar_w,         scol)
            safe_addstr(win, row, 9+bar_w, f" {score:.1f}", scol | curses.A_BOLD)

# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(200)
    init_colors()

    state = AppState()

    def bg_fetch():
        fetch_all_news(state)

    t = threading.Thread(target=bg_fetch, daemon=True)
    t.start()

    last_tick = time.time()

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        with state.lock:
            draw_topbar(stdscr, state)
            if state.loading:
                msg = "Fetching news feeds... please wait"
                safe_addstr(stdscr, h//2, (w-len(msg))//2, msg, curses.color_pair(C_NEU) | curses.A_BOLD)
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

        elif key in (ord('1'),): state.tab = 0
        elif key in (ord('2'),): state.tab = 1
        elif key in (ord('3'),): state.tab = 2
        elif key in (ord('4'),): state.tab = 3

        elif key == curses.KEY_UP:
            if state.tab == 0:
                state.sel = max(0, state.sel - 1)
                if state.sel < state.scroll:
                    state.scroll = state.sel
            elif state.tab == 2:
                state.etf_scroll = max(0, state.etf_scroll - 1)

        elif key == curses.KEY_DOWN:
            if state.tab == 0:
                state.sel = min(len(state.news)-1, state.sel + 1)
                if state.sel >= state.scroll + (h - 5):
                    state.scroll = state.sel - (h - 6)
            elif state.tab == 2:
                state.etf_scroll += 1

        elif key in (ord('r'), ord('R')):
            state.loading = True
            state.news    = []
            t2 = threading.Thread(target=bg_fetch, daemon=True)
            t2.start()

        # Auto-refresh every 5 minutes
        if time.time() - last_tick > 300 and not state.loading:
            last_tick = time.time()
            state.loading = True
            t3 = threading.Thread(target=bg_fetch, daemon=True)
            t3.start()

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    print("\nsectornews closed. Run again with: python3 sectornews.py\n")
