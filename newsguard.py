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


# ---------------- Nyhedsjaeger: AI'en vurderer selv, hvilke nyheder kan give gevinst ----------------
# Gratis: Alpaca News API (samme noegler som handel). AI (Haiku) kaldes KUN naar der er nye overskrifter,
# max NEWS_MAX_CALLS_PER_DAY gange og min. NEWS_MIN_GAP_MIN imellem. Signalet bliver et setup ("nyhed_op"),
# som stadig skal igennem Chef, Skeptiker, Risk Manager, stop-loss og 15 %-loftet.
# v2 (brugerens valg 2. nov): loese regler, krypto + aktier. Strammes senere ud fra resultaterne.
import os
NEWS_MAX_CALLS_PER_DAY = int(os.getenv("NEWS_MAX_CALLS_PER_DAY") or 30)
NEWS_MIN_GAP_MIN = float(os.getenv("NEWS_MIN_GAP_MIN") or 10)
NEWS_MAX_AGE_H = 6          # nyheder fra de sidste 6 timer
SIGNAL_MAX_H = 6            # et signal udloeber senest efter 6 timer
NEWS_MIN_STYRKE = int(os.getenv("NEWS_MIN_STYRKE") or 3)  # positiv nyhed med styrke 3+ giver setup
MAX_CHASE_PCT = 0.03        # koeb ikke hvis prisen allerede er steget over 3 % siden vi saa nyheden
MAX_DIP_PCT = 0.01          # og heller ikke hvis den er faldet over 1 % (markedet er uenigt)

HUNTER_INSTR = (
    "Du er nyhedsjaeger. Du faar friske overskrifter og skal TAENKE SELV: hvilke af dem kan faa en af "
    "vores aktier eller krypto-coins til at stige de naeste 1-6 timer? Taenk i led: hvad betyder nyheden for "
    "indtjening eller efterspoergsel, hvem ellers paavirkes (fx NVDA-nyhed paavirker QQQ, ETF-indstroemning "
    "paavirker BTC og ETH, regulering rammer al krypto), og er det en overraskelse eller ventet? Du maa gerne "
    "give et signal ogsaa paa et symbol, nyheden ikke naevner direkte, hvis du kan forklare sammenhaengen. "
    "Styrke 3 = sandsynlig paavirkning, 4-5 = klar og overraskende. Vurder aerligt om den allerede er i prisen. "
    "Medtag kun symboler fra listen.")
HUNTER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["vurderinger"], "properties": {
    "vurderinger": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["symbol", "retning", "styrke", "allerede_i_prisen", "gyldig_timer", "begrundelse"],
        "properties": {
            "symbol": {"type": "string"},
            "retning": {"type": "string", "enum": ["op", "ned", "neutral"]},
            "styrke": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
            "allerede_i_prisen": {"type": "boolean"},
            "gyldig_timer": {"type": "integer", "enum": [1, 2, 3, 4]},
            "begrundelse": {"type": "string"}}}}}}


def _api(sym):  # BTC/USD -> BTCUSD (Alpacas nyheds-symbol)
    return sym.replace("/", "")


def _alpaca_news(symbols, since):
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest
    import os
    client = NewsClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    back = {_api(s): s for s in symbols}
    res = client.get_news(NewsRequest(symbols=",".join(back), start=since, limit=50))
    out = []
    for n in res.data.get("news", []):
        t = n.created_at if n.created_at.tzinfo else n.created_at.replace(tzinfo=timezone.utc)
        out.append({"id": str(n.id), "tid": t.isoformat(), "overskrift": n.headline,
                    "resume": (n.summary or "")[:200], "symboler": [back[s] for s in (n.symbols or []) if s in back]})
    return out


def hunt(state, ask, symbols, budget_ok, test_mode=False):
    """Koeres en gang pr. runde mens boersen er aaben. Henter nye nyheder (gratis) og spoerger kun AI,
    naar der er nye. Gemmer signaler i state["nyhedsjaeger"]["signaler"]. Returnerer en kort status."""
    nj = state.setdefault("nyhedsjaeger", {})
    sig = nj.setdefault("signaler", {})
    t = _now()
    for s in list(sig):  # ryd udloebne signaler
        if datetime.fromisoformat(sig[s]["udloeber"]) < t:
            sig.pop(s)
    day = t.date().isoformat()
    if nj.get("dag") != day:
        nj.update(dag=day, kald_i_dag=0)
    seen = set(nj.get("set_ids", []))
    try:
        if test_mode:
            news = [{"id": "t1", "tid": t.isoformat(), "overskrift": "Nvidia hæver forventninger efter rekordsalg",
                     "resume": "", "symboler": ["NVDA"]}]
        else:
            news = _alpaca_news(symbols, t - timedelta(hours=NEWS_MAX_AGE_H))
    except Exception as e:
        return {"status": f"nyheder fejlede: {type(e).__name__}"}
    new = [n for n in news if n["id"] not in seen]
    if not new:
        return {"status": "ingen nye nyheder (intet AI-kald)"}
    last = nj.get("sidste_kald")
    if last and (t - datetime.fromisoformat(last)).total_seconds() < NEWS_MIN_GAP_MIN * 60:
        return {"status": f"{len(new)} nye, venter (min. {NEWS_MIN_GAP_MIN} min mellem AI-kald)"}
    if nj["kald_i_dag"] >= NEWS_MAX_CALLS_PER_DAY or not budget_ok():
        return {"status": f"{len(new)} nye, men AI-loft for nyheder/budget naaet"}
    new = sorted(new, key=lambda n: n["tid"], reverse=True)[:20]
    nj["kald_i_dag"] += 1
    nj["sidste_kald"] = t.isoformat()
    nj["set_ids"] = (list(seen) + [n["id"] for n in new])[-300:]
    txt = "\n".join(f"- [{n['tid'][11:16]} UTC] {', '.join(n['symboler']) or '?'}: {n['overskrift']}. {n['resume']}"
                    for n in new)
    try:
        res = {"vurderinger": [{"symbol": "BTC/USD", "retning": "op", "styrke": 3, "allerede_i_prisen": True, "gyldig_timer": 2, "begrundelse": "Test"}, {"symbol": "NVDA", "retning": "op", "styrke": 4, "allerede_i_prisen": False,
                                "gyldig_timer": 2, "begrundelse": "Test"}]} if test_mode else ask("Nyhedsjaeger", HUNTER_INSTR,
                  f"Vores symboler (aktier og krypto): {', '.join(symbols)}. Klokken er {t:%H:%M} UTC.\nNye overskrifter:\n{txt}",
                  HUNTER_SCHEMA)
    except Exception as e:
        return {"status": f"AI-fejl: {type(e).__name__}"}
    for v in res.get("vurderinger", []):
        s = v["symbol"].upper()
        if s not in symbols:
            continue
        old = sig.get(s)
        if old and old["styrke"] > v["styrke"] and old["retning"] == v["retning"]:
            continue  # behold det staerkeste signal
        sig[s] = {**v, "symbol": s, "tid": t.isoformat(), "pris_ved_nyhed": None,
                  "udloeber": (t + timedelta(hours=min(v["gyldig_timer"], SIGNAL_MAX_H))).isoformat()}
    return {"status": f"{len(new)} nye vurderet", "vurderinger": res.get("vurderinger", [])}


def signal_for(state, symbol, price):
    """Returnerer (setup, blok, tekst) for et symbol.
    setup = "nyhed_op" ved positiv nyhed (styrke 3+), hvis kursen ikke er faldet over 1 % eller steget over 3 % siden.
    blok = grund til ikke at koebe ved meget staerk negativ nyhed (4+). tekst = vurderingen til Chef."""
    s = state.get("nyhedsjaeger", {}).get("signaler", {}).get(symbol)
    if not s:
        return "", "", ""
    if s["pris_ved_nyhed"] is None:  # foerste gang vi ser prisen efter nyheden: gem den, bekraeft senere
        s["pris_ved_nyhed"] = price
    move = price / s["pris_ved_nyhed"] - 1
    tekst = (f"Nyhedsjaeger ({s['tid'][11:16]} UTC): {s['retning']} styrke {s['styrke']}/5, "
             f"{'allerede i prisen' if s['allerede_i_prisen'] else 'ikke i prisen endnu'}. {s['begrundelse']} "
             f"Kurs siden nyheden: {move:+.2%}.")
    if s["retning"] == "ned" and s["styrke"] >= 4:
        return "", f"negativ nyhed ({s['styrke']}/5): {s['begrundelse'][:80]}", tekst
    if s["retning"] == "op" and s["styrke"] >= NEWS_MIN_STYRKE and -MAX_DIP_PCT <= move <= MAX_CHASE_PCT:
        return "nyhed_op", "", tekst
    return "", "", tekst
