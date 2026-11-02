"""
Nyhedsvagt: undgaa nye koeb omkring regnskaber og store, negative nyheder.
Gratis kilder: Nasdaq regnskabskalender, Yahoo Finance RSS (aktier), CoinDesk og Cointelegraph RSS (krypto).
Alt er forsvar: fejler en kilde, blokeres intet (botten koerer videre som foer).
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Accept": "application/json, application/xml, text/xml, */*"}
NEWS_HOURS = 12

STOCK_WORDS = ["downgrade", "lawsuit", "sues", "sued", "investigation", "probe", "recall", "cuts guidance",
               "guidance cut", "lowers guidance", "trading halt", "halted", "bankruptcy", "fraud", "plunge",
               "plunges", "sec charges", "antitrust", "subpoena", "short seller"]
CRYPTO_WORDS = ["hack", "hacked", "exploit", "stolen", "drained", "sec sues", "lawsuit", "delist", "halts",
                "halted", "bankruptcy", "insolvency", "outage", "depeg", "crash", "ban ", "banned", "liquidation"]
CRYPTO_NAMES = {"BTC/USD": ["bitcoin", "btc"], "ETH/USD": ["ethereum", "ether", "eth"],
                "SOL/USD": ["solana", "sol"]}
ETFS = {"SPY", "QQQ", "DIA", "IWM", "VTI"}
CRYPTO_FEEDS = ["https://www.coindesk.com/arc/outboundfeeds/rss/", "https://cointelegraph.com/rss"]


def _now():
    return datetime.now(timezone.utc)


def _rss(url, status):
    """Returnerer [(titel, tid)] fra et RSS-feed. Fejl noteres i status og giver tom liste."""
    try:
        r = requests.get(url, headers=UA, timeout=10)
        r.raise_for_status()
        items = []
        for it in ET.fromstring(r.content).iter("item"):
            title = (it.findtext("title") or "").strip()
            try:
                t = parsedate_to_datetime(it.findtext("pubDate") or "")
                t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            except Exception:
                t = _now()
            if title:
                items.append((title, t))
        status[url.split("/")[2]] = f"ok ({len(items)})"
        return items
    except Exception as e:
        status[url.split("/")[2]] = f"fejl: {type(e).__name__}"
        return []


def earnings_symbols(day, cache, status):
    """Symboler med regnskab paa en given dato (Nasdaq-kalender). Gemmes i cache pr. dato."""
    key = day.isoformat()
    if key in cache:
        return cache[key]
    try:
        r = requests.get(f"https://api.nasdaq.com/api/calendar/earnings?date={key}", headers=UA, timeout=10)
        r.raise_for_status()
        rows = ((r.json().get("data") or {}).get("rows")) or []
        syms = sorted({(row.get("symbol") or "").upper() for row in rows if row.get("symbol")})
        cache[key] = syms
        status["nasdaq"] = "ok"
        return syms
    except Exception as e:
        status["nasdaq"] = f"fejl: {type(e).__name__}"
        return []  # caches ikke, saa vi proever igen naeste runde


def _hits(titles, words, names=None):
    out = []
    for title, t in titles:
        low = f" {title.lower()} "
        if _now() - t > timedelta(hours=NEWS_HOURS):
            continue
        if names and not any(re.search(rf"\b{re.escape(n)}\b", low) for n in names + ["crypto", "exchange"]):
            continue
        if any(w in low for w in words):
            out.append(title)
    return out


def check(symbol, is_stock, state, test_mode=False):
    """Returnerer (blok_grund, ekstra_overskrifter, status). blok_grund er "" hvis der ikke er noget."""
    status = {}
    if test_mode:
        return "", ["Test: ingen store nyheder"], {"test": "ok"}
    if is_stock:
        cache = state.setdefault("earnings_cache", {})
        today = _now().date()
        for k in list(cache):  # ryd gamle datoer
            if k < (today - timedelta(days=5)).isoformat():
                cache.pop(k)
        for d in (today - timedelta(days=1), today, today + timedelta(days=1)):
            if symbol in earnings_symbols(d, cache, status):
                return f"regnskab {d.isoformat()}", [], status
        titles = _rss(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US", status)
        hits = [] if symbol in ETFS else _hits(titles, STOCK_WORDS)  # brede ETF'er: overskrifterne er generelle markedsnyheder, saa de bruges kun som info
    else:
        titles = []
        for url in CRYPTO_FEEDS:
            titles += _rss(url, status)
        hits = _hits(titles, CRYPTO_WORDS, CRYPTO_NAMES.get(symbol, [symbol.split("/")[0].lower()]))
        names = CRYPTO_NAMES.get(symbol, [])
        titles = [x for x in titles if any(re.search(rf"\b{re.escape(n)}\b", x[0].lower()) for n in names)]
    recent = [t for t, ts in sorted(titles, key=lambda x: x[1], reverse=True)
              if _now() - ts <= timedelta(hours=24)][:6]
    if hits:
        return f"stor nyhed: {hits[0][:120]}", recent, status
    return "", recent, status
