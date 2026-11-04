"""
Reddit-snak: gratis, ekstra input til Chef og nyhedsjaegeren. ALDRIG et koebssignal alene.

Hvad den goer (helt simpelt):
- Hvert 15. minut henter den de nyeste opslag fra r/wallstreetbets, r/stocks, r/pennystocks (aktier)
  og r/CryptoCurrency (krypto). Det er gratis. Ingen AI-kald, saa det koster 0 kr.
- Den taeller, hvor tit hvert af vores symboler bliver naevnt (fx "ONDS" eller "$ONDS").
- Chef faar en linje som: "Reddit: ONDS naevnt 9 gange sidste 6 t (ca. 2 normalt) - STIGENDE SNAK".

Adgang: uden noegle bruges Reddits offentlige feed. Reddit blokerer det somme tider fra GitHub.
Saa kan man lave en gratis Reddit-app og gemme REDDIT_CLIENT_ID og REDDIT_CLIENT_SECRET som GitHub-secrets.
Fejler Reddit, koerer botten videre som foer (intet blokeres, intet koebes).
"""
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

FETCH_EVERY_MIN = 15
KEEP_H = 24          # gem opslag i 24 timer (til at kende "normalt" niveau)
BUZZ_H = 6           # "nu" = sidste 6 timer
MAX_POSTS = 250
STOCK_SUBS = "wallstreetbets+stocks+pennystocks"
CRYPTO_SUBS = "CryptoCurrency+Bitcoin+ethereum+solana"
UA = {"User-Agent": "linux:paper-tradingbot:v1.0 (read-only research bot)"}

NAMES = {  # firmanavne, som folk skriver i stedet for tickeren (smaa bogstaver)
    "NVDA": ["nvidia"], "AAPL": ["apple"], "MSFT": ["microsoft"], "AMZN": ["amazon"], "GOOGL": ["google", "alphabet"],
    "TSLA": ["tesla"], "AMD": [], "META": [], "SPY": [], "QQQ": [],
    "ONDS": ["ondas"], "RCAT": ["red cat"], "UMAC": ["unusual machines"], "BBAI": ["bigbear"],
    "SOUN": ["soundhound"], "QUBT": ["quantum computing inc"], "RGTI": ["rigetti"], "LUNR": ["intuitive machines"],
    "ACHR": ["archer aviation"], "OUST": ["ouster"],
    "EQIX": ["equinix"], "DLR": ["digital realty"],
    "BTC/USD": ["bitcoin", "btc"], "ETH/USD": ["ethereum", "eth"], "SOL/USD": ["solana", "sol"],
}


def _now():
    return datetime.now(timezone.utc)


def _patterns(symbols):
    out = {}
    for s in symbols:
        pats = []
        if "/" not in s:  # aktie-ticker: skal staa med STORE bogstaver (META, AMD) eller med $ foran
            pats.append(re.compile(rf"(?<![A-Za-z$])\$?{re.escape(s)}(?![A-Za-z])"))
            pats.append(re.compile(rf"\${re.escape(s)}(?![A-Za-z])", re.I))
        for n in NAMES.get(s, []):
            pats.append(re.compile(rf"(?<![a-z]){re.escape(n)}(?![a-z])", re.I))
        out[s] = pats
    return out


def _token():
    cid, sec = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not cid or not sec:
        return None
    r = requests.post("https://www.reddit.com/api/v1/access_token", auth=(cid, sec),
                      data={"grant_type": "client_credentials"}, headers=UA, timeout=10)
    r.raise_for_status()
    return r.json().get("access_token")


def _fetch(subs, token):
    """Returnerer [{id, tid, titel, tekst, score, sub}] - nyeste 100 opslag. Proever JSON, derefter RSS."""
    if token:
        r = requests.get(f"https://oauth.reddit.com/r/{subs}/new?limit=100&raw_json=1",
                         headers={**UA, "Authorization": f"bearer {token}"}, timeout=10)
    else:
        r = requests.get(f"https://www.reddit.com/r/{subs}/new.json?limit=100&raw_json=1", headers=UA, timeout=10)
    if r.ok and r.headers.get("content-type", "").startswith("application/json"):
        out = []
        for c in r.json().get("data", {}).get("children", []):
            d = c.get("data", {})
            out.append({"id": d.get("id"), "tid": datetime.fromtimestamp(d.get("created_utc", 0), timezone.utc),
                        "titel": d.get("title", ""), "tekst": (d.get("selftext") or "")[:500],
                        "score": d.get("score", 0), "sub": d.get("subreddit", "")})
        return out, "json" + (" (app-noegle)" if token else "")
    # reserve: RSS-feedet (bliver sjaeldnere blokeret)
    r2 = requests.get(f"https://www.reddit.com/r/{subs}/new/.rss?limit=100", headers=UA, timeout=10)
    r2.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(r2.content).findall("a:entry", ns):
        try:
            t = datetime.fromisoformat((e.findtext("a:updated", "", ns)).replace("Z", "+00:00"))
        except ValueError:
            continue
        cat = e.find("a:category", ns)
        out.append({"id": e.findtext("a:id", "", ns), "tid": t, "titel": e.findtext("a:title", "", ns),
                    "tekst": "", "score": 0, "sub": cat.get("term", "") if cat is not None else ""})
    return out, f"rss (json gav {r.status_code})"


def update(state, symbols, test_mode=False):
    """Koeres en gang pr. runde. Henter kun nye opslag hvert 15. minut. Returnerer en kort status."""
    rd = state.setdefault("reddit", {})
    t = _now()
    last = rd.get("sidst_hentet")
    if last and (t - datetime.fromisoformat(last)).total_seconds() < FETCH_EVERY_MIN * 60:
        return rd.get("status", "")
    rd["sidst_hentet"] = t.isoformat()
    stocks = [s for s in symbols if "/" not in s]
    crypto = [s for s in symbols if "/" in s]
    pats = _patterns(symbols)
    posts = {p["id"]: p for p in rd.get("opslag", [])}
    kilder = []
    try:
        if test_mode:
            raw = [{"id": f"t{i}", "tid": t, "titel": f"$ONDS to the moon {i}", "tekst": "", "score": 5,
                    "sub": "pennystocks"} for i in range(4)]
            kilder.append("test")
        else:
            token = _token()
            raw = []
            for subs, wanted in ((STOCK_SUBS, stocks), (CRYPTO_SUBS, crypto)):
                if wanted:
                    got, how = _fetch(subs, token)
                    raw += got
                    kilder.append(how)
    except Exception as e:
        rd["status"] = f"Reddit fejlede: {type(e).__name__} {str(e)[:80]}"
        return rd["status"]
    for p in raw:
        txt = f"{p['titel']} {p['tekst']}"
        hits = [s for s, ps in pats.items() if any(x.search(txt) for x in ps)]
        if hits and p["id"]:
            posts[p["id"]] = {"id": p["id"], "tid": p["tid"].isoformat(), "titel": p["titel"][:120],
                              "symboler": hits, "score": p["score"], "sub": p["sub"]}
    cutoff = t - timedelta(hours=KEEP_H)
    keep = sorted((p for p in posts.values() if datetime.fromisoformat(p["tid"]) > cutoff),
                  key=lambda p: p["tid"], reverse=True)[:MAX_POSTS]
    rd["opslag"] = keep
    rd["status"] = f"Reddit ok ({', '.join(kilder)}): {len(raw)} opslag hentet, {len(keep)} naevner vores symboler (24 t)"
    return rd["status"]


def buzz(state, symbol):
    """Tekst til Chef/nyhedsjaeger om Reddit-snak for et symbol. Tom streng hvis ingen snak."""
    posts = [p for p in state.get("reddit", {}).get("opslag", []) if symbol in p["symboler"]]
    if not posts:
        return ""
    t = _now()
    recent = [p for p in posts if datetime.fromisoformat(p["tid"]) > t - timedelta(hours=BUZZ_H)]
    normal = len(posts) / (KEEP_H / BUZZ_H)  # gennemsnit pr. 6 timer over sidste doegn
    hot = len(recent) >= 3 and len(recent) >= 2 * max(normal, 1)
    top = sorted(recent, key=lambda p: p["score"], reverse=True)[:2]
    titles = "; ".join(f"\"{p['titel'][:80]}\" (r/{p['sub']})" for p in top)
    return (f"Reddit: {symbol} naevnt {len(recent)} gange sidste {BUZZ_H} t (ca. {normal:.0f} normalt)"
            f"{' - STIGENDE SNAK' if hot else ''}. {titles} "
            "(Reddit er rygter og hype: brug kun som ekstra info, aldrig som eneste grund til koeb.)").strip()


def new_titles_for_hunter(state, since_iso):
    """Reddit-opslag siden sidste nyhedsjaeger-kald, til at give AI'en ekstra kontekst."""
    posts = [p for p in state.get("reddit", {}).get("opslag", []) if not since_iso or p["tid"] > since_iso]
    posts = sorted(posts, key=lambda p: p["score"], reverse=True)[:10]
    return [f"- [Reddit r/{p['sub']}] {', '.join(p['symboler'])}: {p['titel']}" for p in posts]
