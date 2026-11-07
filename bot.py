"""
Tradingbot v2 til Alpaca PAPER (kun falske penge). Handler BTC, ETH og SOL. Koeres hvert 5. minut.

Hver runde (gratis, ingen AI):
  1. Henter data (1d, 1t, 5m bars + seneste pris) og beregner features (features.py).
  2. Afstemmer position og stop-ordre med Alpaca, flytter trailing stop, tjekker kill-switch.
  3. Afgoer om AI-teamet skal spoerges (kun ved setup eller positions-review, og inden for budget).
AI-runde:
  Scout, Nyheder, Stemning, Chart (Haiku)  ->  Chef (Sonnet)  ->  Skeptiker (Sonnet, tjekliste)
  ->  Risk Manager (Sonnet, absolut veto paa stoerrelse)  ->  koden haandhaever haarde graenser.
Laering: Analytiker skriver lektie (+ eller -) naar 2 lignende handler/fejl er sket.

Koer: python bot.py          (en runde, rigtig paper-handel)
      python bot.py --test   (falske data og falsk AI, ingen noegler, ingen handel)
      python bot.py --dry    (rigtige data og rigtig AI, men ingen ordrer; skriver dry_*-filer)
"""
import csv
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

import features as fx
import newsguard
import reddit

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")
TEST_MODE = "--test" in sys.argv
DRY_RUN = "--dry" in sys.argv or os.getenv("DRY_RUN") == "1"


def env_f(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


# ---------------- Indstillinger (kan overskrives i .env eller som GitHub-variabler) ----------------
# BOT_DIR: en ekstra bot (fx "small") faar sin egen mappe til log/state, saa de to bots aldrig blander data.
BOT_DIR = (os.getenv("BOT_DIR") or "").strip()
_coins = (os.getenv("COINS") or "BTC/USD,ETH/USD,SOL/USD").strip()
CRYPTO = [] if _coins.lower() == "none" else [c.strip() for c in _coins.split(",") if c.strip()]
_stocks = (os.getenv("STOCKS") or "SPY,QQQ,NVDA,AAPL,MSFT,AMZN,META,GOOGL,AMD,TSLA").strip()
STOCKS = [] if _stocks.lower() == "none" else [c.strip().upper() for c in _stocks.split(",") if c.strip()]  # "none" = kun krypto
# Ekstra aktier til paper-test (fx datacenter-ejendomme EQIX, DLR), saettes kun i bot.yml for hoved-botten
for _x in (os.getenv("EXTRA_STOCKS") or "").split(","):
    _x = _x.strip().upper()
    if STOCKS and _x and _x not in STOCKS:
        STOCKS.append(_x)
ORB_SYMBOLS = [c.strip().upper() for c in (os.getenv("ORB_SYMBOLS") or ",".join(STOCKS)).split(",") if c.strip()]
ORB_TIME_STOP_MIN = env_f("ORB_TIME_STOP_MIN", 60)  # ORB: er handlen ikke i plus efter 60 min, saelges den
ORB_MAX_POSITION_PCT = env_f("ORB_MAX_POSITION_PCT", 0.10)  # ORB: max 10 % pr. handel, saa flere kan koere samtidig
MAX_CRYPTO_AI_ROUNDS = int(env_f("MAX_CRYPTO_AI_ROUNDS", 10))  # krypto maa max bruge halvdelen af AI-runderne
ORB_TARGET_R = env_f("ORB_TARGET_R", 2.0)   # maal = 2 x risikoen
ORB_HOLD_WINNERS_R = env_f("ORB_HOLD_WINNERS_R", 0)  # >0: ORB-handler med mindst saa meget gevinst (i R) beholdes over natten
SCAN_GAINERS = int(env_f("SCAN_GAINERS", 0))      # >0: find dagens stoerste stigere hos Alpaca og handl op til saa mange ekstra aktier
SCAN_MIN_PRICE = env_f("SCAN_MIN_PRICE", 5.0)    # ingen penny-aktier (under 5 USD)
SCAN_MIN_PCT = env_f("SCAN_MIN_PCT", 5.0)        # skal vaere steget mindst 5 % i dag
SCAN_END = tuple(int(x) for x in (os.getenv("SCAN_END") or "11:00").split(":"))  # New York-tid: sidste scanning og sidste raket-koeb
ORB_TREND_FILTER = (os.getenv("ORB_TREND_FILTER") or "SPY").strip().upper().replace("NONE", "")  # ingen ORB naar SPY's dagstrend er ned
MARKET_FILTER = (os.getenv("MARKET_FILTER") or "").strip().upper().replace("NONE", "")  # fx QQQ: ingen nye aktiekoeb naar markedet er under dagens snitpris
ORB_BREAKEVEN_R = env_f("ORB_BREAKEVEN_R", 0.5)  # ORB: stop flyttes til koebsprisen ved denne gevinst (i R)
BENCHMARK = (os.getenv("BENCHMARK") or (CRYPTO + STOCKS + ["BTC/USD"])[0]).strip()  # koeb-og-behold sammenlignes med denne
COINS = CRYPTO + STOCKS  # alle handlede symboler; aktier har ingen "/" og handles kun naar boersen er aaben


def is_stock(sym):
    return "/" not in sym
MODEL_FAST = "claude-haiku-5-5"      # Scout, Nyheder, Stemning, Chart
MODEL_SMART = "claude-sonnet-5-5"    # Chef, Skeptiker, Risk Manager, Analytiker
PRICES = {MODEL_FAST: (0.10, 0.50), MODEL_SMART: (2.00, 10.00)}  # USD per 1M tokens (ind, ud)

MONTHLY_BUDGET_USD = env_f("MONTHLY_BUDGET_USD", 7.50)   # haard graense for AI-forbrug
MAX_AI_ROUNDS_PER_DAY = int(env_f("MAX_AI_ROUNDS_PER_DAY", 20))
AI_COOLDOWN_MIN = env_f("AI_COOLDOWN_MIN", 10)            # min. tid mellem to koebs-vurderinger
SAME_SETUP_RETRY_MIN = env_f("SAME_SETUP_RETRY_MIN", 60)  # samme coin + samme setup: spoerg AI max hver time
HOLD_REVIEW_MIN = env_f("HOLD_REVIEW_MIN", 60)            # Chef ser paa aaben position max hver time

RISK_PER_TRADE_PCT = env_f("RISK_PER_TRADE_PCT", 0.005)  # tab ved stop = 0,5 % af kontoen (ved fuld conviction)
MAX_POSITION_PCT = env_f("MAX_POSITION_PCT", 0.15)       # haardt loft paa positionsstoerrelse (per coin)
MAX_TOTAL_EXPOSURE_PCT = env_f("MAX_TOTAL_EXPOSURE_PCT", 0.40)  # samlet loft for alle coins tilsammen
TEST_BUY_PCT = env_f("TEST_BUY_PCT", 0.01)                # engangs-testkoeb via koeb_test.flag
STOP_ATR_MULT = env_f("STOP_ATR_MULT", 2.0)              # stop = entry - 2 x ATR(1t)
MIN_STOP_PCT, MAX_STOP_PCT = 0.01, 0.06
CRYPTO_MIN_STOP_PCT = env_f("CRYPTO_MIN_STOP_PCT", 0.02)  # krypto har ca. 0,5 % gebyr pr. handel: ingen smaa stop
CRYPTO_MIN_ROOM_PCT = env_f("CRYPTO_MIN_ROOM_PCT", 0.03)  # krypto: mindst 3 % op til naeste modstand foer koeb
TRAIL_ATR_MULT = env_f("TRAIL_ATR_MULT", 2.5)            # trailing stop = hoejeste pris - 2,5 x ATR
BREAKEVEN_AT_R = env_f("BREAKEVEN_AT_R", 1.0)            # flyt stop til entry naar +1R
MAX_DAILY_LOSS_PCT = env_f("MAX_DAILY_LOSS_PCT", 0.02)   # -2 % i dag = ingen nye koeb i dag
MAX_DRAWDOWN_PCT = env_f("MAX_DRAWDOWN_PCT", 0.08)       # -8 % fra top = stop nye koeb (HALT)
# Aggressiv stil (bot nr. 5): ekstra tekst til Chef/Skeptiker/Risk Manager. Tom = normal, forsigtig stil.
STYLE = (os.getenv("STRATEGY_STYLE") or "").strip()
SKEPTIC_VETO_FLAGS = int(env_f("SKEPTIC_VETO_FLAGS", 5))  # saa mange flag fra Skeptikeren = automatisk veto
# Automatisk bremse: tjekker hele tiden om den aggressive stil virker. Taber de seneste handler samlet,
# halveres stoerrelsen, indtil de seneste handler igen giver plus. 0 = slaaet fra.
AUTO_BRAKE = os.getenv("AUTO_BRAKE") == "1"
AUTO_BRAKE_TRADES = int(env_f("AUTO_BRAKE_TRADES", 10))   # kig paa de seneste 10 lukkede handler
AUTO_BRAKE_FACTOR = env_f("AUTO_BRAKE_FACTOR", 0.5)        # bremse = halv stoerrelse

LESSONS_NEG, LESSONS_POS = 5, 3
PERF_TRADES = 30

PREFIX = "test_" if TEST_MODE else "dry_" if DRY_RUN else ""
DATA_DIR = BASE / BOT_DIR if BOT_DIR else BASE
DATA_DIR.mkdir(exist_ok=True)
TRADES_CSV = DATA_DIR / f"{PREFIX}trades.csv"
CLOSED_CSV = DATA_DIR / f"{PREFIX}closed_trades.csv"
STATE_FILE = DATA_DIR / f"{PREFIX}state.json"
LESSONS_FILE = DATA_DIR / f"{PREFIX}lessons.md"
LOG_DIR = DATA_DIR / "logs"
STATE_VERSION = 3

CSV_FIELDS = [
    "tid_utc", "coin", "pris", "regime", "setups", "ai_koert", "scout", "nyheder", "stemning", "chart",
    "chef_beslutning", "chef_conviction", "chef_grund", "skeptiker_veto", "skeptiker_grund",
    "risk_godkendt", "risk_faktor", "risk_grund", "handling", "antal", "usd", "stop_loss",
    "konto_vaerdi", "bot_afkast_pct", "koeb_og_behold_vaerdi", "koeb_og_behold_afkast_pct",
    "dagens_pnl_pct", "drawdown_pct", "api_forbrug_usd_md", "note",
]
CLOSED_FIELDS = ["coin", "entry_tid", "exit_tid", "setup", "regime", "conviction", "entry", "exit", "stop_initial",
                 "r_multiple", "pnl_usd", "exit_aarsag", "timer"]


def now():
    return datetime.now(timezone.utc)


def ts(dt=None):
    return (dt or now()).strftime("%Y-%m-%d %H:%M")


# ---------------- State (atomisk skrivning) ----------------
def load_state():
    s = {}
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            bad = STATE_FILE.with_suffix(".korrupt.json")
            STATE_FILE.replace(bad)
            print(f"state.json var oedelagt, gemt som {bad.name}. Starter forfra.")
    if s.get("version", 1) < 2:  # migrering fra v1
        if s.get("entry_price") and s.get("stop_price"):
            s["pos"] = {"entry": s["entry_price"], "stop": s["stop_price"], "stop_initial": s["stop_price"],
                        "high": s["entry_price"], "entry_tid": s.get("start_time", ts()), "setup": "v1",
                        "regime": "ukendt", "conviction": 3}
        for k in ("entry_price", "stop_price", "entry_reason", "entry_fp"):
            s.pop(k, None)
        s["version"] = 2
    if s.get("version", 1) < 3:  # migrering fra kun-BTC til flere coins
        btc = {k: s.pop(k) for k in ("pos", "last_entry_eval", "last_hold_review", "last_review_price") if k in s}
        s.setdefault("coins", {})["BTC/USD"] = btc
        s["version"] = STATE_VERSION
    s.setdefault("coins", {})
    s.setdefault("learned", [])
    s.setdefault("pending", s.pop("pending_failures", {}))
    return s


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(STATE_FILE)


def append_csv(path, fields, row):
    if path.exists():  # ny kolonne-struktur: gem den gamle fil (fx trades_v2.csv) i stedet for at blande
        with open(path, encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        if header != fields:
            n = 1
            while path.with_name(f"{path.stem}_v{n}.csv").exists():
                n += 1
            path.replace(path.with_name(f"{path.stem}_v{n}.csv"))
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def log_row(row):
    append_csv(TRADES_CSV, CSV_FIELDS, row)


def log_json(record):
    """Fuld, struktureret log til senere backtest af beslutningerne. En fil per maaned."""
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / f"{PREFIX}decisions-{now():%Y-%m}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ---------------- Data ----------------
def _bars(raw):
    return [{"t": b.timestamp, "o": float(b.open), "h": float(b.high), "l": float(b.low),
             "c": float(b.close), "v": float(b.volume)} for b in raw]


def fake_bars(n, step_h, start_price):
    import random
    p, out, t0 = start_price, [], now() - timedelta(hours=n * step_h)
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0.0002, 0.006 * step_h ** 0.5)
        out.append({"t": t0 + timedelta(hours=i * step_h), "o": o, "h": max(o, p) * 1.002,
                    "l": min(o, p) * 0.998, "c": p, "v": random.uniform(50, 150)})
    return out


FAKE_START = {"BTC/USD": 60000, "ETH/USD": 3000, "SOL/USD": 150, "SPY": 650, "QQQ": 580, "NVDA": 180, "AAPL": 250}


def get_market_data(SYMBOL):
    """Returnerer (daily, hourly, m5, pris). Pris = seneste handel, ellers sidste 5m-lukning."""
    if TEST_MODE:
        d = fake_bars(200, 24, FAKE_START.get(SYMBOL, 100))
        h = fake_bars(720, 1, d[-1]["c"])
        m = fake_bars(288, 1 / 12, h[-1]["c"])
        return d, h, m, h[-1]["c"]
    if is_stock(SYMBOL):
        return get_stock_data(SYMBOL)
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest, CryptoLatestQuoteRequest, CryptoLatestTradeRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    c = CryptoHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))

    def get(tf, delta):
        return _bars(c.get_crypto_bars(CryptoBarsRequest(symbol_or_symbols=SYMBOL, timeframe=tf,
                                                         start=now() - delta)).data[SYMBOL])
    daily = get(TimeFrame.Day, timedelta(days=200))
    hourly = get(TimeFrame.Hour, timedelta(days=31))
    m5 = get(TimeFrame(5, TimeFrameUnit.Minute), timedelta(hours=26))
    price = m5[-1]["c"]
    try:
        # Midtpris af bud/udbud opdateres hele tiden. Seneste handel kan vaere timer gammel paa Alpacas lille boers.
        q = c.get_crypto_latest_quote(CryptoLatestQuoteRequest(symbol_or_symbols=SYMBOL))[SYMBOL]
        bid, ask = float(q.bid_price), float(q.ask_price)
        if bid > 0 and ask > 0 and (ask - bid) / bid < 0.01:
            price = (bid + ask) / 2
        else:
            price = float(c.get_crypto_latest_trade(CryptoLatestTradeRequest(symbol_or_symbols=SYMBOL))[SYMBOL].price)
    except Exception as e:
        print(f"Seneste pris utilgaengelig ({type(e).__name__}), bruger 5m-lukning")
    if len(hourly) < 100 or len(daily) < 30:
        raise RuntimeError(f"For lidt data for {SYMBOL}: {len(daily)} dag-bars, {len(hourly)} time-bars")
    return daily, hourly, m5, price


def get_stock_data(SYMBOL):
    """Aktier via gratis IEX-data. Time-bars findes kun i aabningstiden, saa '24h'-features daekker ca. 3 handelsdage."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.data.enums import DataFeed
    c = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))

    def get(tf, delta):
        return _bars(c.get_stock_bars(StockBarsRequest(symbol_or_symbols=SYMBOL, timeframe=tf, feed=DataFeed.IEX,
                                                       start=now() - delta)).data[SYMBOL])
    daily = get(TimeFrame.Day, timedelta(days=300))
    hourly = get(TimeFrame.Hour, timedelta(days=60))
    m5 = get(TimeFrame(5, TimeFrameUnit.Minute), timedelta(days=5))
    price = m5[-1]["c"]
    try:
        q = c.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=SYMBOL, feed=DataFeed.IEX))[SYMBOL]
        bid, ask = float(q.bid_price), float(q.ask_price)
        if bid > 0 and ask > 0 and (ask - bid) / bid < 0.005:
            price = (bid + ask) / 2
    except Exception as e:
        print(f"Aktiekurs utilgaengelig ({type(e).__name__}), bruger 5m-lukning")
    if len(hourly) < 100 or len(daily) < 30:
        raise RuntimeError(f"For lidt data for {SYMBOL}: {len(daily)} dag-bars, {len(hourly)} time-bars")
    return daily, hourly, m5, price


def get_news(symbol):
    if TEST_MODE:
        return ["Bitcoin ETF har stor indstroemning", "Fed holder renten uaendret"]
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest
        client = NewsClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
        res = client.get_news(NewsRequest(symbols=symbol.replace("/", ""), limit=8, start=now() - timedelta(hours=24)))
        return [n.headline for n in res.data.get("news", [])][:8] or ["Ingen nyheder sidste doegn"]
    except Exception as e:
        return [f"Nyheder utilgaengelige ({type(e).__name__})"]


def get_fear_greed():
    if TEST_MODE:
        return "55 (Greed)"
    try:
        d = requests.get("https://api.alternative.me/fng/?limit=7", timeout=10).json()["data"]
        return ", ".join(f"{x['value']} ({x['value_classification']})" for x in d) + " (nyeste foerst, 7 dage)"
    except Exception:
        return "ukendt"


# ---------------- AI ----------------
FAKE = {  # svar i --test, saa hele koebs-kaeden koeres igennem
    "signal": "bullish", "styrke": 2, "vurdering": "test", "beslutning": "KOEB", "conviction": 4,
    "setup": "trend_pullback", "invalidering": "test", "grund": "test", "veto": False,
    "godkend": True, "size_faktor": 0.8, "aarsag": "test", "lektie": "Test-lektie",
    "mod_hoejere_timeframe": False, "modstand_for_taet": False, "hoej_volatilitet": False,
    "overkoebt": False, "svag_volumen": False, "ligner_tabslektie": False, "daarlig_historik": False,
}


class Brain:
    def __init__(self, state):
        self.state = state
        month, day = now().strftime("%Y-%m"), now().strftime("%Y-%m-%d")
        if state.get("budget_month") != month:
            state["budget_month"], state["budget_used"] = month, 0.0
        if state.get("ai_day") != day:
            state["ai_day"], state["ai_rounds_today"], state["ai_spend_today"] = day, 0, 0.0
            state["ai_crypto_today"] = 0
        self.client = None
        if not TEST_MODE:
            import anthropic
            self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=3, timeout=60)

    def money_ok(self):
        """Kun pengegraenserne (bruges af nyhedsjaegeren, som ikke taeller med i de 20 AI-runder)."""
        return (self.state["budget_used"] < MONTHLY_BUDGET_USD - 0.05
                and self.state["ai_spend_today"] < MONTHLY_BUDGET_USD / 31 * 2)

    def budget_ok(self):
        """Maanedsloft + jaevn fordeling over maaneden, saa budgettet ikke bruges op paa en dag."""
        days = 31
        daily_allow = MONTHLY_BUDGET_USD / days * 2
        return (self.state["budget_used"] < MONTHLY_BUDGET_USD - 0.05
                and self.state["ai_spend_today"] < daily_allow
                and self.state["ai_rounds_today"] < MAX_AI_ROUNDS_PER_DAY)

    def ask(self, role, model, instructions, data, schema, max_tokens=500):
        if TEST_MODE:
            return {k: FAKE.get(k, "test") for k in schema["properties"]}
        kw = dict(model=model, max_tokens=max_tokens,
                  system=(f"Du er '{role}' i et lille team, der handler krypto og aktier ({', '.join(COINS)}) paa en paper-konto "
                          f"(kun falske penge). {instructions} Svar kort paa dansk (max 30 ord per tekstfelt)."),
                  messages=[{"role": "user", "content": data}],
                  output_config={"effort": "low" if model == MODEL_FAST else "medium",
                                 "format": {"type": "json_schema", "schema": schema}})
        kw["thinking"] = {"type": "disabled"} if model == MODEL_FAST else {"type": "between_tools"}
        try:
            resp = self.client.messages.create(**kw)
        except Exception as e:  # fx hvis modellen ikke accepterer thinking-indstillingen: proev uden
            if type(e).__name__ != "BadRequestError" or "thinking" not in str(e):
                raise
            kw.pop("thinking")
            resp = self.client.messages.create(**kw)
        pin, pout = PRICES[model]
        cost = (resp.usage.input_tokens * pin + resp.usage.output_tokens * pout) / 1_000_000
        self.state["budget_used"] += cost
        self.state["ai_spend_today"] += cost
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"{role} afviste forespoergslen")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(f"{role} loeb toer for tokens")
        return json.loads(next(b.text for b in resp.content if b.type == "text"))


def obj(props, required=None):
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


S_STR, S_BOOL = {"type": "string"}, {"type": "boolean"}
S_INT = {"type": "integer"}
ANALYST = obj({"signal": {"type": "string", "enum": ["bullish", "neutral", "bearish"]},
               "styrke": {"type": "integer", "enum": [1, 2, 3]}, "vurdering": S_STR})


def run_analysts(brain, f, news, fng, coin):
    tr = f"Coin: {coin}. " + fx.summary_text(f)
    jobs = {
        "scout": ("Du giver et hurtigt, nøgternt overblik: regime, trend paa tvaers af tidsrammer, og om "
                  "markedet er roligt eller urolig.", tr),
        "nyheder": (f"Du vurderer om nyhederne paavirker {coin} de naeste 1-24 timer. De fleste nyheder er stoej: "
                    "giv neutral, medmindre en nyhed klart flytter markedet.", "Overskrifter: " + " | ".join(news)),
        "stemning": ("Du laeser Fear & Greed. Ekstrem frygt kan vaere koebsmulighed, ekstrem graadighed er "
                     "risiko. Vaegt aendringen over 7 dage hoejere end niveauet.", f"Fear & Greed (krypto-indeks{'; for en aktie kun et groft maal for risikolyst' if is_stock(coin) else ''}): {fng}"),
        "chart": ("Du er teknisk analytiker. Vurder momentum-kvalitet (ADX, efficiency, ROC), afstand til "
                  "stoette/modstand i ATR og om et koeb her har god risk/reward.", tr),
    }
    return {k: brain.ask(k.capitalize(), MODEL_FAST, ins, data, ANALYST, 300) for k, (ins, data) in jobs.items()}


# ---------------- Laering og performance ----------------
def read_lessons():
    if not LESSONS_FILE.exists():
        return ""
    lines = [l[2:].strip() for l in LESSONS_FILE.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    pos = [l for l in lines if "[+]" in l][-LESSONS_POS:]
    neg = [l for l in lines if "[+]" not in l][-LESSONS_NEG:]
    return "\n".join(neg + pos)


def closed_trades():
    if not CLOSED_CSV.exists():
        return []
    with open(CLOSED_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def brake_factor():
    """Automatisk tjek: (faktor, tekst). Min. 5 lukkede handler; er summen af de seneste N i minus = bremse."""
    if not AUTO_BRAKE:
        return 1.0, ""
    tr = closed_trades()[-AUTO_BRAKE_TRADES:]
    if len(tr) < 5:
        return 1.0, f"auto-tjek: kun {len(tr)} handler, fuld fart"
    pnl = sum(float(t.get("pnl_usd") or 0) for t in tr)
    wins = sum(1 for t in tr if float(t.get("pnl_usd") or 0) > 0)
    if pnl < 0:
        return AUTO_BRAKE_FACTOR, f"auto-tjek: seneste {len(tr)} handler {pnl:+.0f} USD ({wins} vundet), BREMSE (halv stoerrelse)"
    return 1.0, f"auto-tjek: seneste {len(tr)} handler {pnl:+.0f} USD ({wins} vundet), fuld fart"


def perf_summary():
    tr = closed_trades()[-PERF_TRADES:]
    if not tr:
        return "Ingen lukkede handler endnu."
    rs = [float(t["r_multiple"]) for t in tr]
    wins = [r for r in rs if r > 0]
    out = [f"Sidste {len(rs)} handler: winrate {len(wins) / len(rs):.0%}, gns. R {sum(rs) / len(rs):+.2f}, "
           f"samlet R {sum(rs):+.1f}, stoerste tab {min(rs):+.2f}R."]
    groups = {}
    for t in tr:
        groups.setdefault(f"{t['setup']} i {t['regime']}", []).append(float(t["r_multiple"]))
    for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out.append(f"- {k}: {len(v)} handler, winrate {sum(1 for r in v if r > 0) / len(v):.0%}, gns. R {sum(v) / len(v):+.2f}")
    return "\n".join(out)


def analyze_outcome(brain, state, kind, key, details):
    """Analytiker: skriver KUN en lektie naar 2 lignende tilfaelde er sket (tab, gevinst eller teknisk fejl).
    Maa aldrig selv crashe botten."""
    try:
        full = f"{kind}: {key}"
        if full in state["learned"]:
            return None
        events = state["pending"].setdefault(full, [])
        events.append(f"{ts()}: {details[:600]}")
        state["pending"][full] = events[-4:]
        if len(events) < 2:
            print(f"Analytiker: 1. tilfaelde af '{full}', venter paa et lignende.")
            return None
        if brain is None or not brain.budget_ok():
            return None
        positive = kind == "gevinst"
        res = brain.ask(
            "Analytiker", MODEL_SMART,
            ("To lignende handler er nu endt med GEVINST. Find hvad de havde til faelles, og skriv EN konkret, "
             "testbar regel om hvornaar dette setup boer foretraekkes." if positive else
             "Den samme slags fejl/tab er nu sket 2 gange. Find den faelles aarsag og skriv EN konkret, testbar "
             "regel (med tal fra features, fx 'Koeb ikke breakout naar rel_volumen_1h < 1,5'). Ved teknisk fejl: "
             "skriv hvad der skal tjekkes eller rettes.") + " Gentag ikke en eksisterende lektie.",
            f"Type: {kind} ({key})\nDe 2 tilfaelde:\n" + "\n".join(events[-2:]) +
            f"\n\nPerformance:\n{perf_summary()}\n\nEksisterende lektier:\n{read_lessons() or 'ingen'}",
            obj({"aarsag": S_STR, "lektie": S_STR}), 400)
        new = not LESSONS_FILE.exists()
        with open(LESSONS_FILE, "a", encoding="utf-8") as f:
            if new:
                f.write("# Lektier fra Analytikeren\n\nSkrives kun efter 2 lignende tilfaelde. [+] = hvad der virker, "
                        "[-] = hvad der skal undgaas.\n\n")
            f.write(f"- {ts()} [{'+' if positive else '-'}] [{full}] {res['lektie']} (aarsag: {res['aarsag']})\n")
        state["pending"].pop(full, None)
        if kind == "teknisk fejl":
            state["learned"].append(full)
        return res["lektie"]
    except Exception as e:
        print(f"Analytiker fejlede: {type(e).__name__}: {e}")
        return None


# ---------------- Broker ----------------
class Broker:
    """Alle metoder tager coin som 'BTC/USD'. Positioner hos Alpaca hedder 'BTCUSD'."""

    def __init__(self):
        self.paper_only = TEST_MODE or DRY_RUN
        if TEST_MODE:
            self.fake = {"equity": 100000.0, "qty": {}}
            return
        from alpaca.trading.client import TradingClient
        self.t = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)  # ALTID paper

    def market_open(self):
        if TEST_MODE:
            return True
        try:
            return bool(self.t.get_clock().is_open)
        except Exception:
            return False

    def equity(self):
        return self.fake["equity"] if TEST_MODE else float(self.t.get_account().equity)

    def position_qty(self, coin):
        if TEST_MODE:
            return self.fake["qty"].get(coin, 0.0)
        try:
            return float(self.t.get_open_position(coin.replace("/", "")).qty)
        except Exception:
            return 0.0

    def open_stop_orders(self, coin):
        if TEST_MODE:
            return []
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        return [o for o in self.t.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[coin]))
                if str(o.side).lower().endswith("sell")]

    def cancel_open_orders(self, coin):
        if self.paper_only:
            return
        for o in self.open_stop_orders(coin):
            self.t.cancel_order_by_id(o.id)
        for _ in range(10):  # annullering tager et oejeblik; ellers er aktierne stadig 'laast' ved salg
            if not self.open_stop_orders(coin):
                break
            time.sleep(1)

    def place_stop(self, coin, qty, stop):
        if self.paper_only:
            return
        from alpaca.trading.requests import StopLimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        dec = 2 if stop >= 10 or is_stock(coin) else 4
        self.t.submit_order(StopLimitOrderRequest(symbol=coin, qty=qty, side=OrderSide.SELL,
                                                  time_in_force=TimeInForce.GTC, stop_price=round(stop, dec),
                                                  limit_price=round(stop * 0.99, dec)))

    def buy(self, coin, usd, price, stop):
        if is_stock(coin):  # hele aktier, saa stop-ordren kan vaere GTC
            usd = int(usd / price) * price
            if usd <= 0:
                return 0.0
        if self.paper_only:
            if TEST_MODE:
                self.fake["qty"][coin] = usd / price
            return usd / price
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        if is_stock(coin):
            order = self.t.submit_order(MarketOrderRequest(symbol=coin, qty=int(round(usd / price)),
                                                           side=OrderSide.BUY, time_in_force=TimeInForce.DAY))
        else:
            order = self.t.submit_order(MarketOrderRequest(symbol=coin, notional=round(usd, 2),
                                                           side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
        for _ in range(15):  # vent til koebet er HELT fyldt, ellers afviser Alpaca stop-ordren som 'wash trade'
            time.sleep(2)
            try:
                st = str(self.t.get_order_by_id(order.id).status).lower()
                if st.endswith("filled") and "partially" not in st:
                    break
            except Exception:
                pass
        qty = self.position_qty(coin)
        if qty > 0:
            try:
                self.place_stop(coin, qty, stop)
            except Exception as e:  # positionen gemmes alligevel; naeste runde genskaber stop-ordren
                print(f"{coin}: stop-ordre fejlede efter koeb ({e}); proever igen naeste runde")
        return qty

    def sell_all(self, coin):
        qty = self.position_qty(coin)
        if self.paper_only:
            if TEST_MODE:
                self.fake["qty"][coin] = 0.0
            return qty
        self.cancel_open_orders(coin)
        self.t.close_position(coin.replace("/", ""))
        return qty

    def last_sell_price(self, coin):
        """Fyldt pris paa seneste salg (fx stop ramt mellem to runder)."""
        if self.paper_only:
            return None
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            for o in self.t.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[coin], limit=10)):
                if str(o.side).lower().endswith("sell") and o.filled_avg_price:
                    return float(o.filled_avg_price)
        except Exception:
            pass
        return None


# ---------------- Risiko ----------------
def stop_frac(coin, price, atr):
    """Stop-afstand som brøk af prisen: 2 x ATR, mindst 1 % (aktier) / 2 % (krypto), max 6 %."""
    lo = MIN_STOP_PCT if is_stock(coin) else CRYPTO_MIN_STOP_PCT
    return min(max(STOP_ATR_MULT * atr / price, lo), MAX_STOP_PCT)


def compute_size(coin, equity, price, atr, conviction, regime):
    """Volatilitets- og conviction-justeret stoerrelse. Returnerer (usd, stop, stop_pct)."""
    stop_pct = stop_frac(coin, price, atr)
    conv = {1: 0.25, 2: 0.4, 3: 0.6, 4: 0.8, 5: 1.0}.get(int(conviction), 0.5)
    vol = 0.5 if regime == "high_vol" else 1.0
    risk_usd = equity * RISK_PER_TRADE_PCT * conv * vol
    usd = min(risk_usd / stop_pct, equity * MAX_POSITION_PCT) * brake_factor()[0]
    return usd, price * (1 - stop_pct), stop_pct


def risk_gate(state, equity):
    """Kill-switch i kode. Returnerer (ok_til_nye_koeb, grund, dagens_pnl, drawdown)."""
    day = now().strftime("%Y-%m-%d")
    if state.get("eq_day") != day:
        state["eq_day"], state["day_start_equity"] = day, equity
    state["peak_equity"] = max(state.get("peak_equity", equity), equity)
    day_pnl = equity / state["day_start_equity"] - 1
    dd = equity / state["peak_equity"] - 1
    if dd <= -MAX_DRAWDOWN_PCT:
        state["halted"] = True
    if state.get("halted"):
        if os.getenv("RESET_HALT") == "1":
            state["halted"], state["peak_equity"] = False, equity
        else:
            return False, f"HALT: drawdown {dd:.1%} (saet RESET_HALT=1 for at starte igen)", day_pnl, dd
    if day_pnl <= -MAX_DAILY_LOSS_PCT:
        return False, f"Dagligt tabsloft ramt ({day_pnl:.1%})", day_pnl, dd
    return True, "", day_pnl, dd


def manage_position(broker, state, cs, coin, f, qty, brain, row):
    """Afstemning med Alpaca + trailing stop + software-stop. Returnerer True hvis positionen blev lukket."""
    pos, price, atr = cs.get("pos"), f["pris"], f["atr_1h"]
    plan = cs.get("plan")
    if qty > 0 and not pos and plan and (now() - datetime.fromisoformat(plan["plan_tid"])).total_seconds() < 3600:
        # koebet gik igennem, selvom ordren fejlede: brug den plan, der blev gemt foer koebet
        pos = cs["pos"] = {k: v for k, v in plan.items() if k != "plan_tid"}
        pos["qty"] = qty
        cs.pop("plan", None)
        row["note"] = f"Position fundet efter ordrefejl: plan ({pos['setup']}) genskabt"
    if qty > 0 and not pos:  # position uden state (fx manuel handel): adopter den med ATR-stop
        stop = price * (1 - stop_frac(coin, price, atr))
        pos = cs["pos"] = {"entry": price, "stop": stop, "stop_initial": stop, "high": price, "qty": qty,
                           "entry_tid": ts(), "setup": "adopteret", "regime": f["regime"], "conviction": 3}
        row["note"] = "Ukendt position adopteret"
    if qty <= 0 and pos:  # stop ramt hos Alpaca mellem to runder
        exit_price = broker.last_sell_price(coin) or pos["stop"]
        close_trade(brain, state, cs, coin, exit_price, "stop ramt (Alpaca)")
        row.update(handling="STOP RAMT", note=f"Solgt ca. {exit_price:.0f}")
        return True
    if qty <= 0:
        return False
    if price <= pos["stop"]:  # sikkerhedsnet
        broker.sell_all(coin)
        close_trade(brain, state, cs, coin, price, "stop (software)")
        row.update(handling="STOP-LOSS SALG", antal=round(qty, 6))
        return True
    if (f.get("vwap") and f.get("sidste_5m_luk") and f["sidste_5m_luk"] < f["vwap"] and price < f["vwap"]
            and str(pos.get("entry_tid", "")).startswith(now().strftime("%Y-%m-%d"))):
        # Raket der vender: en hel 5-min-periode lukker under dagens snitpris = saelgerne har taget over. Ud med det samme.
        broker.sell_all(coin)
        close_trade(brain, state, cs, coin, price, "raket vendt (under dagens snitpris)")
        row.update(handling="SALG (raket vendt)", antal=round(qty, 6))
        return True
    if pos.get("target") and price >= pos["target"]:  # ORB: tag gevinst ved maalet
        broker.sell_all(coin)
        close_trade(brain, state, cs, coin, price, "maal naaet")
        row.update(handling="SALG (maal)", antal=round(qty, 6))
        return True
    if pos.get("setup") == "adopteret" and is_stock(coin) and cs.get("orb_day") == ny_now().date().isoformat():
        pos["luk_foer_luk"] = True  # ORB-koeb hvis stop-ordre fejlede: stadig en dagshandel
    if pos.get("luk_foer_luk") and price <= pos["entry"]:  # tidsstop: et godt breakout virker hurtigt
        try:
            age_min = (now() - datetime.strptime(pos["entry_tid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)).total_seconds() / 60
        except (KeyError, ValueError):
            age_min = 0
        if age_min >= ORB_TIME_STOP_MIN:
            broker.sell_all(coin)
            close_trade(brain, state, cs, coin, price, f"tidsstop ({ORB_TIME_STOP_MIN:.0f} min uden gevinst)")
            row.update(handling="SALG (tidsstop)", antal=round(qty, 6))
            return True
    if pos.get("luk_foer_luk") and ny_time() >= (15, 50):  # ORB er en dagshandel: ud foer boersen lukker
        r_now = (price - pos["entry"]) / max(pos["entry"] - pos["stop_initial"], 1e-9)
        if (ORB_HOLD_WINNERS_R > 0 and r_now >= ORB_HOLD_WINNERS_R and pos.get("setup") == "orb"
                and coin not in state.get("scan", {}).get("alle", [])):  # raketter (ofte gearede ETF'er) er kun dagshandler
            # Stor vinder: behold den over natten som almindelig trend-handel med trailing stop.
            # Stoppet laases mindst halvvejs op i gevinsten, saa et fald i morgen ikke kan blive et tab.
            lock = pos["entry"] + 0.5 * (price - pos["entry"])
            pos.pop("luk_foer_luk", None)
            pos.pop("target", None)
            pos["setup_oprindelig"] = "orb"
            if lock - pos["stop"] > 0:
                broker.cancel_open_orders(coin)
                broker.place_stop(coin, qty, lock)
                pos["stop"] = lock
            row.update(handling="BEHOLD (ORB-vinder)", stop_loss=round(pos["stop"], 2),
                       note=f"ORB-vinder +{r_now:.1f}R beholdes over natten, stop laast til {pos['stop']:.2f}")
            return False
        broker.sell_all(coin)
        close_trade(brain, state, cs, coin, price, "dagens slut (ORB)")
        row.update(handling="SALG (dagens slut)", antal=round(qty, 6))
        return True
    pos["high"] = max(pos["high"], price)
    r_unit = pos["entry"] - pos["stop_initial"]
    new_stop = pos["stop"]
    be_at = ORB_BREAKEVEN_R if pos.get("luk_foer_luk") else BREAKEVEN_AT_R  # ORB: beskyt gevinsten tidligt (standard +0,5R)
    if price >= pos["entry"] + be_at * r_unit:
        new_stop = max(new_stop, pos["entry"], pos["high"] - TRAIL_ATR_MULT * atr)
    if new_stop - pos["stop"] > 0.25 * atr:
        broker.cancel_open_orders(coin)
        broker.place_stop(coin, qty, new_stop)
        row["note"] = f"Stop flyttet {pos['stop']:.0f} til {new_stop:.0f}"
        pos["stop"] = new_stop
    elif not broker.paper_only and not broker.open_stop_orders(coin):
        broker.place_stop(coin, qty, pos["stop"])
        row["note"] = "Manglende stop-ordre genskabt"
    row["stop_loss"] = round(pos["stop"], 2)
    return False


def close_trade(brain, state, cs, coin, exit_price, reason):
    pos = cs.pop("pos", None)
    if not pos:
        return
    r_unit = max(pos["entry"] - pos["stop_initial"], 1e-9)
    r = (exit_price - pos["entry"]) / r_unit
    try:
        hours = (now() - datetime.strptime(pos["entry_tid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)).total_seconds() / 3600
    except Exception:
        hours = 0
    rec = {"coin": coin, "entry_tid": pos["entry_tid"], "exit_tid": ts(), "setup": pos["setup"], "regime": pos["regime"],
           "conviction": pos.get("conviction", ""), "entry": round(pos["entry"], 2), "exit": round(exit_price, 2),
           "stop_initial": round(pos["stop_initial"], 2), "r_multiple": round(r, 2),
           "pnl_usd": round((exit_price - pos["entry"]) * pos.get("qty", 0), 2), "exit_aarsag": reason,
           "timer": round(hours, 1)}
    append_csv(CLOSED_CSV, CLOSED_FIELDS, rec)
    if abs(r) >= 0.3:  # ca. break-even er hverken en lektie eller en fejl
        analyze_outcome(brain, state, "gevinst" if r > 0 else "tab", f"{pos['setup']} i {pos['regime']}",
                        f"{coin}: {reason}. R={r:+.2f}. Entry {pos['entry']:.0f}, exit {exit_price:.0f}. "
                        f"Grund til koeb: {pos.get('grund', '?')}. Features ved koeb: {pos.get('features', '')}")


# ---------------- Beslutning ----------------
def need_ai(cs, f, holding):
    t = now()
    if holding:
        last = cs.get("last_hold_review")
        moved = abs(f["pris"] - cs.get("last_review_price", f["pris"])) >= f["atr_1h"]
        due = not last or (t - datetime.fromisoformat(last)).total_seconds() >= HOLD_REVIEW_MIN * 60
        return (due or moved), "positions-review"
    if not f["setups"] and not TEST_MODE:
        return False, "intet setup"
    last = cs.get("last_entry_eval")
    if last and (t - datetime.fromisoformat(last)).total_seconds() < AI_COOLDOWN_MIN * 60:
        return False, "cooldown"
    if (last and cs.get("last_entry_setups") == sorted(f["setups"])
            and (t - datetime.fromisoformat(last)).total_seconds() < SAME_SETUP_RETRY_MIN * 60):
        return False, "samme setup vurderet for under en time siden"
    return True, "setup: " + ", ".join(f["setups"] or ["test"])


def run_ai(brain, state, cs, coin, f, holding, equity, can_buy, block_reason, row, rec):
    """Hele AI-kaeden. Returnerer handling: ('KOEB', usd, stop, chef) / ('SAELG',) / ('HOLD',)."""
    state["ai_rounds_today"] += 1
    if not is_stock(coin):
        state["ai_crypto_today"] = state.get("ai_crypto_today", 0) + 1
    r = run_analysts(brain, f, get_news(coin) + f.pop("_ekstra_nyheder", []), get_fear_greed(), coin)
    for k, v in r.items():
        row[k] = f"{v['signal']}({v['styrke']}): {v['vurdering']}"
    rec["analytikere"] = r
    summary = "\n".join(f"{k}: {v['signal']} styrke {v['styrke']} - {v['vurdering']}" for k, v in r.items())
    lessons, perf = read_lessons() or "ingen endnu", perf_summary()
    if AUTO_BRAKE:
        perf += f"\n{brake_factor()[1]}"
    pos = cs.get("pos")
    pos_txt = (f"Vi EJER {coin}: koebt {pos['entry']:.0f} ({pos['setup']}), stop {pos['stop']:.0f}, nu "
               f"{(f['pris'] / pos['entry'] - 1) * 100:+.2f}% = {(f['pris'] - pos['entry']) / max(pos['entry'] - pos['stop_initial'], 1e-9):+.2f}R."
               if holding else f"Vi ejer IKKE {coin}.")
    if not holding and not can_buy:
        pos_txt += f" Nye koeb er SPAERRET: {block_reason}."
    setups = f["setups"] or ["ingen"]
    others = [c for c, v in state["coins"].items() if v.get("pos") and c != coin]
    pos_txt += f" Andre aabne positioner: {', '.join(others) or 'ingen'}."
    ctx = (f"COIN: {coin}\n{pos_txt}\n\nFEATURES: {fx.summary_text(f)}\n\nANALYTIKERE:\n{summary}\n\n"
           f"NYHEDSJAEGER: {f.get('_nyhedsjaeger') or 'intet signal'}\n\n"
           f"REDDIT: {f.get('_reddit') or 'ingen snak'}\n\n"
           f"PERFORMANCE:\n{perf}\n\nLEKTIER:\n{lessons}")

    chef = brain.ask(
        "Chef", MODEL_SMART,
        f"Du traeffer beslutningen for {coin}. KOEB kun hvis vi ikke ejer {coin}, et af de fundne setups er gyldigt, og der er "
        f"mindst 1,2R plads til naeste modstand. Setup nyhed_op betyder, at nyhedsjaegeren fandt en positiv "
        "nyhed: taenk selv over om den kan loefte kursen yderligere, og koeb hvis du tror det. SAELG kun hvis vi ejer {coin} og grunden til koebet er brudt. Ellers "
        "HOLD. Conviction 1-5 styrer stoerrelsen (5 = sjaeldent, alt flugter). Angiv hvad der ville bevise dig forkert."
        + (f" STIL: {STYLE}" if STYLE else ""),
        ctx, obj({"beslutning": {"type": "string", "enum": ["KOEB", "SAELG", "HOLD"]},
                  "conviction": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                  "setup": {"type": "string", "enum": setups + (["ingen"] if "ingen" not in setups else [])},
                  "invalidering": S_STR, "grund": S_STR}), 400)
    rec["chef"] = chef
    row.update(chef_beslutning=chef["beslutning"], chef_conviction=chef["conviction"], chef_grund=chef["grund"])
    if chef["beslutning"] == "SAELG" and holding:
        return ("SAELG",)
    if chef["beslutning"] != "KOEB" or holding or not can_buy:
        return ("HOLD",)

    # Faste tjek i kode, som Skeptikeren skal forholde sig til
    hard = []
    if f["trend_1d"] < 0:
        hard.append("daglig trend er ned")
    if f["afstand_modstand_atr"] < STOP_ATR_MULT * 1.0:
        hard.append(f"modstand kun {f['afstand_modstand_atr']} ATR over (under 1R)")
    if f["rsi_1h"] > 70:
        hard.append("RSI 1h over 70")
    sk = brain.ask(
        "Skeptiker", MODEL_SMART,
        "Du er systematisk djaevlens advokat. Gaa tjeklisten igennem og saet hvert punkt til true, hvis det er et "
        "reelt problem for netop dette koeb. Brug kun veto hvis et punkt er klart alvorligt. Vaer ikke negativ "
        "for at vaere negativ: et koeb med god risk/reward og flugtende tidsrammer skal igennem."
        + (f" STIL: {STYLE}" if STYLE else ""),
        f"Chefen vil KOEBE {coin} (setup {chef['setup']}, conviction {chef['conviction']}) fordi: {chef['grund']}\n"
        f"Invalidering: {chef['invalidering']}\nFaste regel-advarsler: {hard or 'ingen'}\n\n{ctx}",
        obj({"mod_hoejere_timeframe": S_BOOL, "modstand_for_taet": S_BOOL, "hoej_volatilitet": S_BOOL,
             "overkoebt": S_BOOL, "svag_volumen": S_BOOL, "ligner_tabslektie": S_BOOL,
             "daarlig_historik": S_BOOL, "veto": S_BOOL, "grund": S_STR}), 400)
    flags = [k for k, v in sk.items() if v is True and k != "veto"]
    veto = sk["veto"] or len(flags) >= SKEPTIC_VETO_FLAGS or len(hard) >= 3
    rec["skeptiker"], rec["regel_advarsler"] = sk, hard
    row.update(skeptiker_veto=veto, skeptiker_grund=f"{sk['grund']} [flag: {', '.join(flags) or 'ingen'}]")
    if veto:
        return ("VETO",)

    usd, stop, stop_pct = compute_size(coin, equity, f["pris"], f["atr_1h"], chef["conviction"], f["regime"])
    rm = brain.ask(
        "Risk Manager", MODEL_SMART,
        "Du har absolut veto paa stoerrelse. Du kan godkende, reducere (size_faktor 0-1) eller blokere. Reducer ved "
        "daarlig nylig performance i dette regime/setup, hoej volatilitet, eller naar kontoen allerede har tabt i "
        "dag. Du kan aldrig oege stoerrelsen." + (f" STIL: {STYLE}" if STYLE else ""),
        f"Foreslaaet koeb af {coin}: {usd:.0f} USD ({usd / equity:.1%} af kontoen), stop {stop:.0f} ({stop_pct:.2%} under), "
        f"risiko {usd * stop_pct:.0f} USD ({usd * stop_pct / equity:.2%} af kontoen). Konto {equity:.0f}. "
        f"Dagens resultat {row.get('dagens_pnl_pct')}%, drawdown fra top {row.get('drawdown_pct')}%.\n"
        f"Setup {chef['setup']}, conviction {chef['conviction']}, regime {f['regime']}.\n"
        f"Skeptikerens flag: {flags or 'ingen'}\n\n{ctx}",
        obj({"godkend": S_BOOL, "size_faktor": {"type": "number"}, "grund": S_STR}), 300)
    factor = min(max(float(rm["size_faktor"]), 0.0), 1.0) if rm["godkend"] else 0.0
    rec["risk_manager"] = rm
    row.update(risk_godkendt=rm["godkend"], risk_faktor=round(factor, 2), risk_grund=rm["grund"])
    usd *= factor
    if usd < 50:
        return ("RISK_BLOK",)
    return ("KOEB", usd, stop, chef)


def main():
    state = load_state()
    brain = None
    try:
        brain = Brain(state)
        run_round(state, brain)
    except Exception as e:  # fx Alpaca nede: en fejl-raekke for hele runden
        details = f"{type(e).__name__}: {str(e)[:300]}"
        print(f"FEJL i runden: {details}\n{traceback.format_exc()}")
        analyze_outcome(brain, state, "teknisk fejl", f"{type(e).__name__}: {str(e)[:40]}", details)
        log_row({"tid_utc": ts(), "coin": "ALLE", "handling": f"FEJL ({type(e).__name__})", "note": details[:200]})
        log_json({"tid": now().isoformat(), "fejl": details})
    finally:
        state["budget_used"] = round(state.get("budget_used", 0.0), 6)
        if AUTO_BRAKE:
            state["auto_tjek"] = brake_factor()[1]  # vises i OVERSIGT.md
        save_state(state)


def read_test_flag():
    try:
        with open(DATA_DIR / "koeb_test.flag") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def exposure(state):
    """Samlet USD i aabne positioner (entry-pris x antal)."""
    return sum(v["pos"]["entry"] * v["pos"].get("qty", 0) for v in state["coins"].values() if v.get("pos"))


def is_inverse_name(name):
    """Omvendt fond: navnet siger Bear, Inverse, UltraShort, Short (ikke 'Short-Term') eller VIX (frygt-indeks)."""
    import re
    n = (name or "").lower()
    return bool(re.search(r"\b(bear|inverse|ultrashort|vix)\b", n) or re.search(r"\bshort\b(?!-term)", n))


def scan_gainers(state, stocks_open):
    """Dagens raketter: henter Alpacas liste over dagens stoerste stigere (gratis, intet AI-kald).
    Kun aktier over SCAN_MIN_PRICE, steget mindst SCAN_MIN_PCT %, og blandt de 100 mest handlede (saa de er nemme
    at koebe og saelge). Listen bygges 9:35-11:00 New York-tid og holdes resten af dagen. Aabne positioner fra
    tidligere scanninger handles altid videre, saa de aldrig mister deres stop-styring."""
    scan = state.setdefault("scan", {})
    held = [c for c, v in state["coins"].items() if v.get("pos") and is_stock(c) and c in scan.get("alle", [])]
    if SCAN_GAINERS <= 0 or TEST_MODE:
        return held
    day = ny_now().date().isoformat()
    if scan.get("dag") != day:
        scan.update(dag=day, syms=[], sidst="")
    t = ny_time()
    due = not scan.get("sidst") or (now() - datetime.fromisoformat(scan["sidst"])).total_seconds() >= 600
    if stocks_open and (9, 35) <= t <= SCAN_END and due and len(scan["syms"]) < SCAN_GAINERS:
        scan["sidst"] = now().isoformat()
        try:
            h = {"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")}
            base = "https://data.alpaca.markets/v1beta1/screener/stocks"
            gainers = requests.get(f"{base}/movers", params={"top": 50}, headers=h, timeout=15).json().get("gainers", [])
            active = {x["symbol"] for x in requests.get(f"{base}/most-actives", params={"by": "trades", "top": 100},
                                                         headers=h, timeout=15).json().get("most_actives", [])}
            found = []
            for g in gainers:
                sym = str(g.get("symbol", "")).upper()
                if (sym.isalpha() and sym in active and sym not in STOCKS and sym not in scan["syms"]
                        and float(g.get("price") or 0) >= SCAN_MIN_PRICE and float(g.get("percent_change") or 0) >= SCAN_MIN_PCT):
                    found.append(f"{sym} +{float(g['percent_change']):.0f}%")
                    scan["syms"].append(sym)
                    if len(scan["syms"]) >= SCAN_GAINERS:
                        break
            scan["alle"] = sorted(set(scan.get("alle", [])) | set(scan["syms"]))[-60:]
            # Omvendte fonde (stiger naar markedet falder): find dem paa navnet hos Alpaca, fx "... Bear 3X Shares".
            th = {"APCA-API-KEY-ID": h["APCA-API-KEY-ID"], "APCA-API-SECRET-KEY": h["APCA-API-SECRET-KEY"]}
            for sym in scan["syms"]:
                if sym in scan.setdefault("navne", {}):
                    continue
                try:
                    scan["navne"][sym] = requests.get(f"https://paper-api.alpaca.markets/v2/assets/{sym}", headers=th,
                                                      timeout=10).json().get("name", "")
                except Exception:
                    scan["navne"][sym] = ""
            scan["omvendt"] = sorted({c for c, n in scan["navne"].items() if is_inverse_name(n)} | set(scan.get("omvendt", [])))
            print("Raket-scanner:", ", ".join(found) or "ingen nye")
        except Exception as e:  # scanneren maa aldrig stoppe botten
            print("Raket-scanner fejlede:", type(e).__name__, str(e)[:100])
    return list(dict.fromkeys(scan["syms"] + held))


def run_round(state, brain):
    broker = Broker()
    equity = broker.equity()
    can_buy, block, day_pnl, dd = risk_gate(state, equity)
    stocks_open = broker.market_open() if STOCKS else False
    state["marked"] = {}
    if MARKET_FILTER and stocks_open and not TEST_MODE and ny_time() >= (9, 45):
        # Proff-regel "handl med markedet": falder hele markedet (under dagens snitpris), koeber vi ingen nye aktier.
        try:
            m5 = get_stock_data(MARKET_FILTER)[2]
            mv, ml = vwap_today(m5)
            if mv and ml:
                state["marked"] = {"symbol": MARKET_FILTER, "pris": round(ml, 2), "snitpris": round(mv, 2), "svag": ml < mv}
            print("Markeds-filter:", state["marked"])
        except Exception as e:  # filteret maa aldrig stoppe botten
            print("Markeds-filter fejlede:", type(e).__name__)
    state["marked_trend"] = None
    if ORB_TREND_FILTER and stocks_open and not TEST_MODE:
        # Uge 1: 9 ORB-tab paa en fredag hvor hele markedet faldt. Er SPY i en faldende dagstrend, laves ingen morgenkoeb.
        try:
            d, _, _, p = get_stock_data(ORB_TREND_FILTER)
            state["marked_trend"] = fx.trend_state([b["c"] for b in d] + [p])
            print("ORB-trendfilter:", ORB_TREND_FILTER, state["marked_trend"])
        except Exception as e:  # filteret maa aldrig stoppe botten
            print("ORB-trendfilter fejlede:", type(e).__name__)
    for sym in reversed(scan_gainers(state, stocks_open)):  # raketter foerst, saa de faar pengene foer de faste aktier
        for lst in (COINS, STOCKS, ORB_SYMBOLS):
            if sym in lst:
                lst.remove(sym)
            lst.insert(0, sym)
    news_syms = CRYPTO + (STOCKS if stocks_open else [])  # krypto doegnet rundt, aktier kun naar boersen er aaben
    if news_syms:  # Reddit-snak: gratis, intet AI-kald, kun ekstra info til Chef og nyhedsjaeger
        try:
            print("Reddit:", reddit.update(state, news_syms, TEST_MODE))
        except Exception as e:  # Reddit maa aldrig stoppe botten
            print("Reddit fejlede:", type(e).__name__)
    if news_syms:  # nyhedsjaeger: gratis nyheds-tjek, AI kun naar der er nye overskrifter
        nj = newsguard.hunt(state, lambda role, ins, data, schema: brain.ask(role, MODEL_FAST, ins, data, schema, 2000),
                            news_syms, brain.money_ok, TEST_MODE,
                            lambda since: reddit.new_titles_for_hunter(state, since))
        print("Nyhedsjaeger:", json.dumps(nj, ensure_ascii=False))
        if nj.get("vurderinger"):
            log_json({"tid": now().isoformat(), "coin": "NYHEDSJAEGER", **nj})
    for coin in COINS:
        if is_stock(coin) and not stocks_open:
            continue  # boersen er lukket: stop-ordren hos Alpaca passer paa en evt. position
        row = {"tid_utc": ts(), "coin": coin, "dagens_pnl_pct": round(day_pnl * 100, 3), "drawdown_pct": round(dd * 100, 3)}
        rec = {"tid": now().isoformat(), "coin": coin, "mode": "test" if TEST_MODE else "dry" if DRY_RUN else "paper"}
        cs = state["coins"].setdefault(coin, {})
        try:
            run_coin(state, brain, broker, coin, cs, equity, can_buy, block, row, rec)
        except Exception as e:  # en coin der fejler maa ikke stoppe de andre
            details = f"{coin}: {type(e).__name__}: {str(e)[:300]}"
            print(f"FEJL: {details}\n{traceback.format_exc()}")
            analyze_outcome(brain, state, "teknisk fejl", f"{type(e).__name__}: {str(e)[:40]}", details)
            row.update(handling=f"FEJL ({type(e).__name__})", note=details[:200])
            rec["fejl"] = details
        eq = broker.equity()
        bh = state["start_equity"] * state.get("last_btc_price", state.get("start_price", 1)) / state.get("start_price", 1) \
            if "start_equity" in state else eq
        row.update(konto_vaerdi=round(eq, 2), bot_afkast_pct=round((eq / state.get("start_equity", eq) - 1) * 100, 3),
                   koeb_og_behold_vaerdi=round(bh, 2),
                   koeb_og_behold_afkast_pct=round((bh / state.get("start_equity", eq) - 1) * 100, 3),
                   api_forbrug_usd_md=round(state.get("budget_used", 0.0), 4))
        if cs.get("pos") and not row.get("stop_loss"):
            row["stop_loss"] = round(cs["pos"]["stop"], 4)
        rec["row"], rec["api_forbrug_usd_md"] = row, state.get("budget_used")
        log_row(row)
        log_json(rec)
        print(json.dumps(row, ensure_ascii=False))


def ny_now():
    from zoneinfo import ZoneInfo
    return now().astimezone(ZoneInfo("America/New_York"))


def ny_time():
    t = ny_now()
    return (t.hour, t.minute)


def vwap_today(m5):
    """Dagens snitpris (VWAP): gennemsnitsprisen vaegtet med hvor meget der er handlet, fra 9:30 New York-tid.
    Over snitprisen = koeberne styrer (stiger). Under = saelgerne styrer (falder). Returnerer (vwap, sidste lukkede 5m-luk)."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    d = ny_now().date()
    today = [b for b in m5 if b["t"].astimezone(ny).date() == d and (b["t"].astimezone(ny).hour, b["t"].astimezone(ny).minute) >= (9, 30)]
    vol = sum(b["v"] for b in today)
    if not today or vol <= 0:
        return None, None
    closed = [b for b in today if b["t"] + timedelta(minutes=5) <= now()]
    return sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in today) / vol, (closed[-1]["c"] if closed else None)


def raket_signal(m5, price, vwap):
    """Raket-koeb (ren regel): sidste lukkede 5m-bar lukker paa dagens hoejeste, prisen er over dagens snitpris
    og hoejst 1 % over den lukning. Stop = dagens snitpris (1-8 % under). 9:45 til SCAN_END New York-tid."""
    t = ny_now()
    if not vwap or not ((9, 45) <= (t.hour, t.minute) <= SCAN_END):
        return None
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    today = [b for b in m5 if b["t"].astimezone(ny).date() == t.date()
             and (b["t"].astimezone(ny).hour, b["t"].astimezone(ny).minute) >= (9, 30)]
    closed = [b for b in today if b["t"] + timedelta(minutes=5) <= now()]
    if len(closed) < 4:
        return None
    last, before = closed[-1], closed[:-1]
    top = max(b["h"] for b in before)
    stop = vwap
    risk = (price - stop) / price
    if last["c"] > top and price > vwap and price <= last["c"] * 1.01 and 0.01 <= risk <= 0.08:
        return stop, {"dagstop": round(top, 2), "vwap": round(vwap, 2), "risiko_pct": round(risk * 100, 1)}
    return None


def orb_signal(m5, price):
    """Opening range breakout (ren regel, ingen AI). Range = de 3 foerste 5m-bars (9:30-9:45 New York-tid).
    Koeb naar sidste lukkede 5m-bar lukker over toppen, og prisen ikke er loebet mere end en halv range vaek.
    Stop = midten af range, maal = ORB_TARGET_R x risiko. Kun 9:45-11:30 New York-tid.
    Returnerer (stop, maal, info) eller None."""
    if TEST_MODE:
        hi, lo = price * 1.001, price * 0.996
        return (hi + lo) / 2, price + ORB_TARGET_R * (price - (hi + lo) / 2), {"test": True}
    t = ny_now()
    if not ((9, 45) <= (t.hour, t.minute) <= (11, 30)):
        return None
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    today = [b for b in m5 if b["t"].astimezone(ny).date() == t.date()]
    rng = [b for b in today if (9, 30) <= (b["t"].astimezone(ny).hour, b["t"].astimezone(ny).minute) < (9, 45)]
    if len(rng) < 3:
        return None
    hi, lo = max(b["h"] for b in rng), min(b["l"] for b in rng)
    closed = [b for b in today if b["t"] + timedelta(minutes=5) <= now() and b not in rng]
    if not closed or hi <= lo:
        return None
    mid = (hi + lo) / 2
    info = {"or_high": round(hi, 2), "or_low": round(lo, 2), "sidste_luk": round(closed[-1]["c"], 2)}
    if closed[-1]["c"] > hi and hi < price <= hi + 0.5 * (hi - lo):
        return mid, price + ORB_TARGET_R * (price - mid), info
    return None


def run_coin(state, brain, broker, coin, cs, equity, can_buy, block, row, rec):
    daily, hourly, m5, price = get_market_data(coin)
    f = fx.compute_features(daily, hourly, m5, price)
    rec["features"] = f
    if coin == BENCHMARK:  # koeb-og-behold sammenlignes med BENCHMARK (BTC i hoved-botten)
        if "start_equity" not in state:
            state.update(start_equity=equity, start_price=price, start_time=now().isoformat())
        state["last_btc_price"] = price
    state.setdefault("start_equity", equity)
    row.update(pris=round(price, 4 if price < 10 else 2), regime=f["regime"], setups=" ".join(f["setups"]))
    raket = is_stock(coin) and coin in state.get("scan", {}).get("alle", [])
    if raket and not TEST_MODE:
        f["vwap"], f["sidste_5m_luk"] = vwap_today(m5)

    qty = broker.position_qty(coin)
    if broker.paper_only and cs.get("pos"):  # test/dry: ingen rigtig position, brug state
        qty = cs["pos"].get("qty", 0.0)
    closed = manage_position(broker, state, cs, coin, f, qty, brain, row)
    holding = bool(cs.get("pos")) and not closed
    room = equity * MAX_TOTAL_EXPOSURE_PCT - exposure(state)
    coin_can_buy, coin_block = can_buy, block
    mk = state.get("marked") or {}
    omvendt = coin in state.get("scan", {}).get("omvendt", [])
    if coin_can_buy and is_stock(coin) and mk.get("svag") and not omvendt:
        coin_can_buy, coin_block = False, f"markedet falder ({mk['symbol']} {mk['pris']} under dagens snitpris {mk['snitpris']})"
    if coin_can_buy and omvendt and mk and not mk.get("svag"):  # plan: omvendte fonde KUN naar markedet falder
        coin_can_buy, coin_block = False, f"omvendt fond, men markedet stiger ({mk['symbol']} over dagens snitpris)"
    if coin_can_buy and room < 50:
        coin_can_buy, coin_block = False, f"samlet loft paa {MAX_TOTAL_EXPOSURE_PCT:.0%} af kontoen naaet"

    action = row.get("handling") or ("HOLD (position)" if holding else "HOLD")
    if coin in ORB_SYMBOLS and not holding and not closed and coin_can_buy and cs.get("orb_day") != ny_now().date().isoformat():
        sig = orb_signal(m5, price)
        if sig and (state.get("marked_trend") or 0) < 0:
            row["note"] = f"ORB-signal, men {ORB_TREND_FILTER} er i en faldende dagstrend: intet morgenkoeb"
            sig = None
        if sig and raket and f.get("vwap") and price < f["vwap"]:  # raket under dagens snitpris: den er ved at falde
            row["note"] = f"ORB-signal, men raketten er under dagens snitpris ({f['vwap']:.2f})"
            sig = None
        if sig:  # planen: ingen ORB ved regnskab, stor negativ nyhed eller staerk negativ nyhedsjaeger-vurdering
            nb, _, ng = newsguard.check(coin, True, state, TEST_MODE)
            nb = nb or newsguard.signal_for(state, coin, price)[1]
            if nb:
                rec["orb_blokeret"] = {"grund": nb, "kilder": ng}
                row["note"] = f"ORB-signal, men sprunget over: {nb}"[:200]
                cs["orb_day"] = ny_now().date().isoformat()
                sig = None
        if sig:
            stop, target, info = sig
            cs["orb_day"] = ny_now().date().isoformat()  # max en ORB-handel pr. dag pr. symbol
            risk_pct = max((price - stop) / price, 0.001)
            usd = min(equity * RISK_PER_TRADE_PCT / risk_pct, equity * min(MAX_POSITION_PCT, ORB_MAX_POSITION_PCT),
                      room) * brake_factor()[0]
            rec["orb"] = info
            bought = buy_with_plan(broker, cs, f, coin, usd, price, stop, "orb", 3, f"ORB breakout {info}", row,
                                   target=None if raket else target, luk_foer_luk=True)  # raketter: intet loft, VWAP-reglen saelger
            if bought > 0:
                row.update(handling="KOEB (ORB)", antal=round(bought, 6), usd=round(bought * price, 2),
                           stop_loss=round(stop, 2), ai_koert="nej (ORB er ren regel)",
                           note=f"ORB: range {info.get('or_low')}-{info.get('or_high')}, maal {target:.2f}")
                rec["handling"] = "KOEB (ORB)"
                return
    today_iso = ny_now().date().isoformat()
    if raket and not holding and not closed and coin_can_buy and today_iso not in (cs.get("raket_day"), cs.get("orb_day")):
        sig = raket_signal(m5, price, f.get("vwap"))
        if sig:
            stop, info = sig
            cs["raket_day"] = ny_now().date().isoformat()  # max et raket-koeb pr. dag pr. aktie
            risk_pct = max((price - stop) / price, 0.001)
            usd = min(equity * RISK_PER_TRADE_PCT / risk_pct, equity * min(MAX_POSITION_PCT, ORB_MAX_POSITION_PCT),
                      room) * brake_factor()[0]
            bought = buy_with_plan(broker, cs, f, coin, usd, price, stop, "raket", 3, f"Raket ny dagstop {info}", row,
                                   target=None, luk_foer_luk=True)
            if bought > 0:
                row.update(handling="KOEB (raket)", antal=round(bought, 6), usd=round(bought * price, 2),
                           stop_loss=round(stop, 2), ai_koert="nej (raket er ren regel)",
                           note=f"Raket: ny top {info['dagstop']}, snitpris {info['vwap']}")
                rec["handling"] = "KOEB (raket)"
                return
    news_setup, news_block, f["_nyhedsjaeger"] = newsguard.signal_for(state, coin, price)
    f["_reddit"] = reddit.buzz(state, coin)
    if f["_reddit"]:
        rec["reddit"] = f["_reddit"]
    if news_setup and news_setup not in f["setups"]:
        f["setups"].append(news_setup)
        row["setups"] = " ".join(f["setups"])
    want, why = need_ai(cs, f, holding)
    if want and not holding and not is_stock(coin) and state.get("ai_crypto_today", 0) >= MAX_CRYPTO_AI_ROUNDS:
        want, why = False, f"krypto har brugt sine {MAX_CRYPTO_AI_ROUNDS} AI-runder i dag (resten er til aktier)"
    if holding and news_block and not closed and cs.get("nyhed_review") != f["_nyhedsjaeger"]:
        cs["nyhed_review"] = f["_nyhedsjaeger"]  # staerk negativ nyhed: Chef ser paa positionen en gang
        want, why = True, f"positions-review ({news_block})"
    if not closed and want and not holding and news_block:
        want, why = False, f"nyhedsjaeger: {news_block}"
    if not closed and want and not coin_can_buy and not holding:
        want, why = False, coin_block
    room_pct = (f["naeste_modstand"] - price) / price
    if not closed and want and not holding and not is_stock(coin) and room_pct < CRYPTO_MIN_ROOM_PCT:
        want, why = False, f"kun {room_pct:.1%} til modstand (krypto kraever {CRYPTO_MIN_ROOM_PCT:.0%} pga. gebyr)"
    if not closed and want and not holding and not is_stock(coin) and f["trend_1d"] < 0 and not TEST_MODE:
        want, why = False, "krypto koebes ikke mod en faldende dagstrend (gebyr ca. 0,5 % kraever medvind)"
    if not closed and want and not holding:  # nyhedsvagt: kun foer nye koeb, aldrig ved aaben position
        block_news, extra_news, ng_status = newsguard.check(coin, is_stock(coin), state, TEST_MODE)
        rec["nyhedsvagt"] = {"blok": block_news, "kilder": ng_status, "overskrifter": extra_news}
        f["_ekstra_nyheder"] = extra_news
        if block_news:
            want, why = False, f"nyhedsvagt: {block_news}"
    if not closed and want and not brain.budget_ok():
        want, why = False, "AI-budget for i dag/maaned brugt"
    rec["ai_grund"] = why
    row["ai_koert"] = why if want and not closed else f"nej ({why})"

    if want and not closed:
        if holding:
            cs["last_hold_review"], cs["last_review_price"] = now().isoformat(), price
        else:
            cs["last_entry_eval"], cs["last_entry_setups"] = now().isoformat(), sorted(f["setups"])
        try:
            result = run_ai(brain, state, cs, coin, f, holding, equity, coin_can_buy, coin_block, row, rec)
        except Exception as e:
            analyze_outcome(brain, state, "teknisk fejl", f"{type(e).__name__}: {str(e)[:40]}",
                            f"AI-kaede fejlede ({coin}): {type(e).__name__}: {str(e)[:300]}")
            result = ("HOLD",)
            row["note"] = f"AI-fejl: {type(e).__name__}"
        if result[0] == "KOEB":
            _, usd, stop, chef = result
            usd = min(usd, room)
            bought = buy_with_plan(broker, cs, f, coin, usd, price, stop, chef["setup"], chef["conviction"],
                                   chef["grund"][:300], row, invalidering=chef.get("invalidering", "")[:200])
            if bought > 0:
                action = "KOEB"
                row.update(antal=round(bought, 6), usd=round(bought * price, 2), stop_loss=round(stop, 4))
            else:
                row["note"] = "Koeb sendt, men ingen position fundet"
        elif result[0] == "SAELG":
            sold = broker.sell_all(coin)
            close_trade(brain, state, cs, coin, price, "Chef solgte")
            action = "SAELG"
            row.update(antal=round(sold, 6))
        elif result[0] == "VETO":
            action = "INGEN HANDEL (Skeptiker-veto)"
        elif result[0] == "RISK_BLOK":
            action = "INGEN HANDEL (Risk Manager)"

    # Engangs-testkoeb (kun BTC): laeg en ny tekst i koeb_test.flag for at koebe ca. 1 % af kontoen.
    flag_id = read_test_flag()
    if (coin == COINS[0] and flag_id and flag_id != state.get("test_kob_id") and not holding and not closed
            and coin_can_buy and action != "KOEB"):
        state["test_kob_id"] = flag_id
        usd = min(equity * TEST_BUY_PCT, room)
        stop = price * (1 - stop_frac(coin, price, f["atr_1h"]))
        bought = buy_with_plan(broker, cs, f, coin, usd, price, stop, "testkoeb", 1,
                               "Manuelt testkoeb for at teste koeb, stop og log", row)
        if bought > 0:
            action = "KOEB (test)"
            row.update(antal=round(bought, 6), usd=round(usd, 2), stop_loss=round(stop, 4),
                       note="Engangs-testkoeb (ca. 1 % af kontoen)")
    row["handling"] = rec["handling"] = action


def buy_with_plan(broker, cs, f, coin, usd, price, stop, setup, conviction, grund, row, **extra):
    """Alle koeb gaar herigennem: planen (stop, maal, regler) gemmes FOER ordren sendes. Fejler ordren,
    men koebet gik alligevel igennem, bruges planen stadig (her eller i naeste runde) i stedet for at gaette."""
    plan = new_pos(f, price, stop, 0.0, setup, conviction, grund)
    plan.update(extra, plan_tid=now().isoformat())
    cs["plan"] = plan
    try:
        bought = broker.buy(coin, usd, price, stop)
    except Exception as e:
        print(f"{coin}: koeb gav fejl ({type(e).__name__}: {str(e)[:200]}); tjekker om positionen findes")
        row["note"] = f"Ordrefejl ved koeb: {type(e).__name__}"
        time.sleep(3)
        bought = broker.position_qty(coin)
    if bought > 0:
        cs["pos"] = {k: v for k, v in plan.items() if k != "plan_tid"}
        cs["pos"]["qty"] = bought
        cs.pop("plan", None)
    return bought


def new_pos(f, price, stop, qty, setup, conviction, grund):
    return {"entry": price, "stop": stop, "stop_initial": stop, "high": price, "qty": qty, "entry_tid": ts(),
            "setup": setup, "regime": f["regime"], "conviction": conviction, "grund": grund,
            "features": {k: f[k] for k in ("trend_1h", "trend_4h", "trend_1d", "rsi_1h", "adx_1h",
                                           "rel_volumen_1h", "afstand_modstand_atr", "atr_percentil_30d")}}


if __name__ == "__main__":
    main()
