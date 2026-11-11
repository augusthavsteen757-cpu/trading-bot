"""
Options-bot (bot nr. 4) til Alpaca PAPER (kun falske penge). Ren regel-bot uden AI (koster 0 kr i API).

Ide (fra TikTok-videoen, gjort forsigtig):
  "Options-hint" = hvor stor en del af dagens optionshandel i en aktie er CALLS (vaedder paa stigning)
  i forhold til PUTS (vaedder paa fald). Botten koeber KUN, naar hintet og kursen peger samme vej:
    - mange calls (>= 65 %) OG aktien er over sit 20-dages snit   = koeb en CALL
    - mange puts  (>= 60 %) OG aktien er under sit 20-dages snit  = koeb en PUT
  Hintet alene er aldrig nok.

Sikkerhed (alt i kode):
  - Max 1 % af kontoen pr. handel. Hele praemien er det, man hoejst kan tabe.
  - Max 4 aabne handler, max 3 nye pr. dag, max 1 pr. aktie pr. dag.
  - Saelg ved +50 % (gevinst), -40 % (tab), 7 dage foer udloeb, efter 14 dage, eller hvis trenden vender.
  - Kill-switch: -3 % paa dagen eller -10 % fra top = ingen nye koeb.
  - Kun 10:00-15:00 New York for koeb. Salg tjekkes hele boersdagen.

Koer: python options_bot.py          (en runde, rigtig paper-handel)
      python options_bot.py --test   (falske data, ingen noegler, ingen handel)
Data gemmes i mappen options/.
"""
import csv
import json
import math
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")
TEST_MODE = "--test" in sys.argv
NY = ZoneInfo("America/New_York")


def env_f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


STOCKS = [s.strip().upper() for s in (os.getenv("OPT_STOCKS") or "SPY,QQQ,NVDA,AAPL,MSFT,AMZN,META,GOOGL,AMD,TSLA").split(",") if s.strip()]
CALL_SHARE_BUY = env_f("OPT_CALL_SHARE", 0.65)    # calls >= 65 % af volumen = bullish hint
PUT_SHARE_BUY = env_f("OPT_PUT_SHARE", 0.60)      # puts >= 60 % = bearish hint (puts bruges ogsaa som forsikring, derfor lidt lavere)
MIN_VOLUME = env_f("OPT_MIN_VOLUME", 3000)        # under 3000 kontrakter i dag = hintet er for tyndt
RISK_PCT = env_f("OPT_RISK_PCT", 0.01)            # max 1 % af kontoen pr. handel
MAX_OPEN = int(env_f("OPT_MAX_OPEN", 4))
MAX_NEW_PER_DAY = int(env_f("OPT_MAX_NEW_PER_DAY", 3))
TAKE_PROFIT = env_f("OPT_TAKE_PROFIT", 0.50)      # +50 %
STOP_LOSS = env_f("OPT_STOP_LOSS", 0.40)          # -40 %
MIN_DTE, MAX_DTE = int(env_f("OPT_MIN_DTE", 14)), int(env_f("OPT_MAX_DTE", 45))  # dage til udloeb ved koeb
EXIT_DTE = int(env_f("OPT_EXIT_DTE", 7))          # saelg senest 7 dage foer udloeb
MAX_HOLD_DAYS = int(env_f("OPT_MAX_HOLD_DAYS", 14))
MAX_SPREAD = env_f("OPT_MAX_SPREAD", 0.10)        # forskel paa koeb/salgspris max 10 % af prisen
MAX_DAILY_LOSS = env_f("OPT_MAX_DAILY_LOSS", 0.03)
MAX_DRAWDOWN = env_f("OPT_MAX_DRAWDOWN", 0.10)
# 11. nov: de to stop-salg (-42 %, -43 %) skete 2 og 25 min efter aabning, hvor optioners koeb/salgspris
# ligger laengst fra hinanden. Foer 10:00 New York saelges kun ved et noedstop (default -60 %).
OPEN_CALM_MIN = int(env_f("OPT_OPEN_CALM_MIN", 30))   # minutter efter 9:30 NY uden almindelige salg
EMERGENCY_STOP = env_f("OPT_EMERGENCY_STOP", 0.60)

DATA_DIR = BASE / "options"
DATA_DIR.mkdir(exist_ok=True)
PREFIX = "test_" if TEST_MODE else ""
TRADES_CSV = DATA_DIR / f"{PREFIX}trades.csv"
CLOSED_CSV = DATA_DIR / f"{PREFIX}closed_trades.csv"
STATE_FILE = DATA_DIR / f"{PREFIX}state.json"
TRADE_FIELDS = ["tid_utc", "aktie", "kurs", "trend", "call_andel", "options_volumen", "hint", "handling",
                "kontrakt", "antal", "pris", "usd", "konto_vaerdi", "dagens_pnl_pct", "note"]
CLOSED_FIELDS = ["aktie", "kontrakt", "type", "entry_tid", "exit_tid", "entry", "exit", "antal",
                 "pnl_usd", "pnl_pct", "exit_aarsag", "dage", "call_andel_ved_koeb"]

DATA_URL = "https://data.alpaca.markets"


def now():
    return datetime.now(timezone.utc)


def ts():
    return now().strftime("%Y-%m-%d %H:%M")


def headers():
    return {"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")}


def append_csv(path, fields, row):
    new = not path.exists()
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"positions": {}, "day": "", "new_today": 0, "traded_today": [], "peak_equity": 0}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(STATE_FILE)


# ---------------- Data ----------------
def stock_trend(sym):
    """'op' hvis kursen er over 20-dages snittet, 'ned' hvis under. Taet paa snittet (+/-0,5 %) = 'flad'."""
    if TEST_MODE:
        return {"SPY": ("op", 600.0), "TSLA": ("ned", 400.0)}.get(sym, ("flad", 100.0))
    start = (now() - timedelta(days=45)).isoformat()
    r = requests.get(f"{DATA_URL}/v2/stocks/{sym}/bars", headers=headers(), timeout=20,
                     params={"timeframe": "1Day", "start": start, "feed": "iex", "limit": 100})
    r.raise_for_status()
    bars = r.json().get("bars") or []
    q = requests.get(f"{DATA_URL}/v2/stocks/{sym}/trades/latest", headers=headers(), timeout=20, params={"feed": "iex"})
    q.raise_for_status()
    price = float(q.json()["trade"]["p"])
    if len(bars) < 21:
        return "flad", price
    today = datetime.now(NY).date().isoformat()
    closes = [b["c"] for b in bars if not b["t"].startswith(today)]  # kun afsluttede dage
    sma20 = sum(closes[-20:]) / 20
    if price > sma20 * 1.005:
        return "op", price
    if price < sma20 * 0.995:
        return "ned", price
    return "flad", price


def option_chain(sym, price):
    """Alle kontrakter 1-45 dage ud, strike +/-20 %. Gratis 'indicative' data fra Alpaca."""
    if TEST_MODE:
        exp = (date.today() + timedelta(days=21)).strftime("%y%m%d")
        out = {}
        for k, (typ, vol, delta) in enumerate([("C", 3000, 0.52), ("C", 1500, 0.35), ("P", 600, -0.48), ("P", 300, -0.30)]):
            strike = int(price * (1 + (0.01 if typ == "C" else -0.01) * (k % 2 + 1)))
            occ = f"{sym}{exp}{typ}{strike * 1000:08d}"
            if sym == "TSLA":  # TSLA i test: mange puts
                vol = 4000 - vol
            out[occ] = {"latestQuote": {"bp": 4.90, "ap": 5.10}, "dailyBar": {"v": vol}, "greeks": {"delta": delta}}
        return out
    today = date.today()
    params = {"feed": "indicative", "limit": 1000,
              "expiration_date_gte": (today + timedelta(days=1)).isoformat(),
              "expiration_date_lte": (today + timedelta(days=MAX_DTE)).isoformat(),
              "strike_price_gte": round(price * 0.8, 2), "strike_price_lte": round(price * 1.2, 2)}
    out = {}
    for _ in range(10):
        r = requests.get(f"{DATA_URL}/v1beta1/options/snapshots/{sym}", headers=headers(), params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        out.update(j.get("snapshots") or {})
        if not j.get("next_page_token"):
            break
        params["page_token"] = j["next_page_token"]
    return out


def parse_occ(occ):
    """'SPY261120C00600000' -> ('SPY', date(2026,11,20), 'C', 600.0)"""
    tail = occ[-15:]
    return occ[:-15], datetime.strptime(tail[:6], "%y%m%d").date(), tail[6], int(tail[7:]) / 1000


def options_hint(chain):
    calls = puts = 0
    for occ, s in chain.items():
        v = (s.get("dailyBar") or {}).get("v") or 0
        if parse_occ(occ)[2] == "C":
            calls += v
        else:
            puts += v
    total = calls + puts
    return (calls / total if total else None), total


def pick_contract(chain, typ):
    """Kontrakt 14-45 dage ud med delta ca. 0,5 (strike taet paa kursen), smal spread."""
    best = None
    for occ, s in chain.items():
        _, exp, t, strike = parse_occ(occ)
        dte = (exp - date.today()).days
        if t != typ or not (MIN_DTE <= dte <= MAX_DTE):
            continue
        q = s.get("latestQuote") or {}
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        delta = abs(float((s.get("greeks") or {}).get("delta") or 0))
        if bid <= 0 or ask < 0.20 or not (0.40 <= delta <= 0.60):
            continue
        mid = (bid + ask) / 2
        if (ask - bid) / mid > MAX_SPREAD:
            continue
        score = abs(delta - 0.5) + dte / 1000  # taettest paa 0,5; ved lighed kortest udloeb
        if best is None or score < best[0]:
            best = (score, occ, bid, ask, delta, dte)
    return best


def read_test_flag():
    try:
        return (DATA_DIR / "koeb_test.flag").read_text().strip()
    except OSError:
        return ""


# ---------------- Broker ----------------
class Broker:
    def __init__(self):
        self.fake = {"equity": 100000.0, "last_equity": 100000.0}
        if TEST_MODE:
            return
        from alpaca.trading.client import TradingClient
        self.t = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)  # ALTID paper

    def clock(self):
        if TEST_MODE:
            return True
        return bool(self.t.get_clock().is_open)

    def account(self):
        if TEST_MODE:
            return self.fake["equity"], self.fake["last_equity"]
        a = self.t.get_account()
        return float(a.equity), float(a.last_equity)

    def option_positions(self):
        """{kontrakt: (antal, aktuel pris pr. aktie, rigtig koebspris)}"""
        if TEST_MODE:
            return {}
        out = {}
        for p in self.t.get_all_positions():
            if "option" in str(p.asset_class).lower():
                out[p.symbol] = (int(float(p.qty)), float(p.current_price or 0), float(p.avg_entry_price or 0))
        return out

    def buy(self, occ, qty, limit):
        if TEST_MODE:
            return True
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        self.t.submit_order(LimitOrderRequest(symbol=occ, qty=qty, side=OrderSide.BUY,
                                              time_in_force=TimeInForce.DAY, limit_price=round(limit, 2)))
        return True

    def sell(self, occ, qty):
        if TEST_MODE:
            return True
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        self.t.submit_order(MarketOrderRequest(symbol=occ, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY))
        return True


# ---------------- Logik ----------------
def manage_exits(broker, state, equity, day_pnl):
    held = broker.option_positions()
    for occ, pos in list(state["positions"].items()):
        if not TEST_MODE and occ not in held:
            # koebsordren blev aldrig fyldt (eller solgt uden for botten): glem den
            if (now() - datetime.fromisoformat(pos["entry_tid"])).total_seconds() > 1800:
                print(f"{occ}: ingen position hos Alpaca, fjernes fra state")
                state["positions"].pop(occ)
            continue
        qty, cur, fill = held.get(occ, (pos["antal"], pos["entry"], pos["entry"]))
        if fill > 0:
            pos["entry"] = fill  # brug den pris, ordren faktisk blev fyldt til
        if qty <= 0 or cur <= 0:
            continue
        pnl = cur / pos["entry"] - 1
        sym, exp, typ, _ = parse_occ(occ)
        dte = (exp - date.today()).days
        held_days = (now() - datetime.fromisoformat(pos["entry_tid"])).days
        reason = None
        ny = now().astimezone(NY)
        calm = (ny.hour * 60 + ny.minute) < (9 * 60 + 30 + OPEN_CALM_MIN)
        if calm and pnl > -EMERGENCY_STOP and not TEST_MODE:
            print(f"{occ}: {pnl:+.0%}, venter til {OPEN_CALM_MIN} min efter aabning med salg (urolige priser)")
            continue
        if pnl >= TAKE_PROFIT:
            reason = f"gevinst +{pnl:.0%}"
        elif pnl <= -STOP_LOSS:
            reason = f"stop {pnl:.0%}"
        elif dte <= EXIT_DTE:
            reason = f"{dte} dage til udloeb"
        elif held_days >= MAX_HOLD_DAYS:
            reason = f"holdt {held_days} dage"
        else:
            try:
                trend, _ = stock_trend(sym)
                if (typ == "C" and trend == "ned") or (typ == "P" and trend == "op"):
                    reason = f"trenden vendte ({trend})"
            except Exception:
                pass
        if not reason:
            continue
        broker.sell(occ, qty)
        state["positions"].pop(occ)
        usd = (cur - pos["entry"]) * 100 * qty
        append_csv(CLOSED_CSV, CLOSED_FIELDS, {
            "aktie": sym, "kontrakt": occ, "type": "call" if typ == "C" else "put", "entry_tid": pos["entry_tid"],
            "exit_tid": now().isoformat(), "entry": pos["entry"], "exit": cur, "antal": qty,
            "pnl_usd": round(usd, 2), "pnl_pct": round(pnl * 100, 1), "exit_aarsag": reason, "dage": held_days,
            "call_andel_ved_koeb": pos.get("call_andel")})
        append_csv(TRADES_CSV, TRADE_FIELDS, {
            "tid_utc": ts(), "aktie": sym, "handling": "SAELG", "kontrakt": occ, "antal": qty, "pris": cur,
            "usd": round(cur * 100 * qty, 2), "konto_vaerdi": round(equity, 2),
            "dagens_pnl_pct": round(day_pnl * 100, 2), "note": f"{reason}, {usd:+.0f} USD"})
        print(f"SOLGT {occ} x{qty}: {reason} ({usd:+.0f} USD)")


def scan_and_buy(broker, state, equity, day_pnl, can_buy, block):
    ny = datetime.now(NY)
    buy_window = TEST_MODE or (ny.hour >= 10 and ny.hour < 15)
    for sym in STOCKS:
        row = {"tid_utc": ts(), "aktie": sym, "konto_vaerdi": round(equity, 2), "dagens_pnl_pct": round(day_pnl * 100, 2)}
        try:
            trend, price = stock_trend(sym)
            chain = option_chain(sym, price)
            share, vol = options_hint(chain)
            row.update(kurs=price, trend=trend, call_andel=round(share, 3) if share is not None else "",
                       options_volumen=vol)
            hint = "ingen"
            if share is not None and vol >= MIN_VOLUME:
                if share >= CALL_SHARE_BUY:
                    hint = "bullish"
                elif 1 - share >= PUT_SHARE_BUY:
                    hint = "bearish"
            row["hint"] = hint
            typ = "C" if hint == "bullish" and trend == "op" else "P" if hint == "bearish" and trend == "ned" else None
            flag = read_test_flag()
            test = (sym == STOCKS[0] and flag and flag != state.get("test_kob_id") and typ is None)
            if test:  # engangs-testkoeb: ny tekst i options/koeb_test.flag = koeb 1 kontrakt for at teste ordren
                typ = "P" if trend == "ned" else "C"
            why = None
            if typ is None:
                why = "hint og trend peger ikke samme vej" if hint != "ingen" else "intet klart hint"
            elif not can_buy:
                why = block
            elif not buy_window:
                why = "koeb kun 10:00-15:00 New York"
            elif sym in state["traded_today"]:
                why = "allerede handlet i dag"
            elif any(parse_occ(o)[0] == sym for o in state["positions"]):
                why = "har allerede en handel i aktien"
            elif len(state["positions"]) >= MAX_OPEN:
                why = f"max {MAX_OPEN} aabne handler"
            elif state["new_today"] >= MAX_NEW_PER_DAY:
                why = f"max {MAX_NEW_PER_DAY} nye i dag"
            if why:
                row.update(handling="VENT", note=why)
                append_csv(TRADES_CSV, TRADE_FIELDS, row)
                continue
            best = pick_contract(chain, typ)
            if not best:
                row.update(handling="VENT", note="ingen god kontrakt (delta 0,4-0,6, 14-45 dage, smal spread)")
                append_csv(TRADES_CSV, TRADE_FIELDS, row)
                continue
            _, occ, bid, ask, delta, dte = best
            qty = 1 if test else math.floor(equity * RISK_PCT / (ask * 100))
            if qty < 1:
                row.update(handling="VENT", note=f"1 kontrakt koster {ask * 100:.0f} USD, mere end {RISK_PCT:.0%} af kontoen")
                append_csv(TRADES_CSV, TRADE_FIELDS, row)
                continue
            broker.buy(occ, qty, ask)
            if test:
                state["test_kob_id"] = flag
            state["positions"][occ] = {"entry": ask, "antal": qty, "entry_tid": now().isoformat(),
                                       "call_andel": row["call_andel"], "trend": trend}
            state["traded_today"].append(sym)
            state["new_today"] += 1
            row.update(handling="KOEB " + ("CALL" if typ == "C" else "PUT"), kontrakt=occ, antal=qty, pris=ask,
                       usd=round(ask * 100 * qty, 2),
                       note=("TESTKOEB, " if test else "") + f"delta {delta:.2f}, {dte} dage, maks tab {ask * 100 * qty:.0f} USD")
            print(f"KOEBT {occ} x{qty} @ {ask}")
        except Exception as e:  # en aktie der fejler maa ikke stoppe de andre
            print(f"FEJL {sym}: {e}\n{traceback.format_exc()}")
            row.update(handling=f"FEJL ({type(e).__name__})", note=str(e)[:200])
        append_csv(TRADES_CSV, TRADE_FIELDS, row)


def main():
    if not TEST_MODE and not os.getenv("ALPACA_API_KEY"):
        print("Mangler ALPACA_API_KEY")
        return
    state = load_state()
    broker = Broker()
    if not broker.clock():
        print("Boersen er lukket. Intet at goere.")
        return
    today = datetime.now(NY).date().isoformat()
    if state.get("day") != today:
        state.update(day=today, new_today=0, traded_today=[])
    equity, last_equity = broker.account()
    state["peak_equity"] = max(state.get("peak_equity") or 0, equity)
    day_pnl = equity / last_equity - 1 if last_equity else 0
    dd = 1 - equity / state["peak_equity"]
    can_buy, block = True, ""
    if day_pnl <= -MAX_DAILY_LOSS:
        can_buy, block = False, f"kill-switch: {day_pnl:.1%} i dag"
    elif dd >= MAX_DRAWDOWN:
        can_buy, block = False, f"kill-switch: {dd:.1%} under toppen"
    manage_exits(broker, state, equity, day_pnl)
    scan_and_buy(broker, state, equity, day_pnl, can_buy, block)
    save_state(state)


if __name__ == "__main__":
    main()
