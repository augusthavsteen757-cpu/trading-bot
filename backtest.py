"""
Backtest af salgsreglerne paa krypto (ingen AI, ingen ordrer, gratis).

Koeber efter de faste regler botten bruger foer AI spoerges:
  setup fundet + dagstrend ikke ned + mindst 3 % op til modstand + modstand mindst 2 ATR vaek.
AI'en (Chef/Skeptiker) er IKKE med, saa det er en test af SALGSREGLERNE, ikke af hele botten.

Hver handel saelges paa to maader paa praecis de samme koeb:
  GAMLE regler: stop-loss, nul-tab-sikring ved +1R, trailing stop.
  NYE regler:   stop-loss, nul-tab-sikring ved +0,5R, trailing stop, tidsstop (60 min uden plus).
Gebyr: 0,25 % pr. side.

Koer: python backtest.py          (rigtige kurser fra Alpaca, kraever noegler i miljoeet)
      python backtest.py --test   (falske kurser)
"""
import os
import sys
from datetime import timedelta

import bot
import features as fx

TEST = "--test" in sys.argv
DAYS = int(os.getenv("BT_DAYS", "30"))
FEE = 0.0025
TRADE_USD = 5000
GAP_H = 6          # mindst 6 timer mellem to koeb i samme coin
SHOW = 10          # vis de 10 nyeste handler
TIME_STOP_MIN = bot.ORB_TIME_STOP_MIN


def fetch(sym):
    if TEST:
        h = bot.fake_bars(24 * (DAYS + 35), 1, bot.FAKE_START.get(sym, 100))
        d = bot.fake_bars(250, 24, h[0]["c"])
        start = h[-24 * (DAYS + 1)]["t"]
        m = []
        for b in h[-24 * (DAYS + 2):]:
            m += bot.fake_bars(12, 1 / 12, b["o"])
            for i, x in enumerate(m[-12:]):
                x["t"] = b["t"] + timedelta(minutes=5 * i)
        return d, h, m, start
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    c = CryptoHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))

    def get(tf, days):
        return bot._bars(c.get_crypto_bars(CryptoBarsRequest(
            symbol_or_symbols=sym, timeframe=tf, start=bot.now() - timedelta(days=days))).data[sym])
    d = get(TimeFrame.Day, DAYS + 230)
    h = get(TimeFrame.Hour, DAYS + 35)
    m = get(TimeFrame(5, TimeFrameUnit.Minute), DAYS + 2)
    return d, h, m, bot.now() - timedelta(days=DAYS)


def simulate(entry, stop0, atr, t0, m5, be_at, time_stop):
    """Gaar 5-minutters bars igennem efter koebet. Returnerer (exit_pris, aarsag, minutter)."""
    stop, high, r = stop0, entry, entry - stop0
    for b in m5:
        if b["t"] < t0:
            continue
        age = (b["t"] - t0).total_seconds() / 60 + 5
        if b["l"] <= stop:
            px = min(stop, b["o"])
            return px, ("nul-tab-sikring" if stop >= entry and stop - entry < 0.25 * atr
                        else "trailing stop" if stop > entry else "stop-loss"), age
        price = b["c"]
        if time_stop and price <= entry and age >= time_stop:
            return price, "tidsstop", age
        high = max(high, price)
        if price >= entry + be_at * r:
            stop = max(stop, entry, high - bot.TRAIL_ATR_MULT * atr)
    return m5[-1]["c"], "stadig aaben", (m5[-1]["t"] - t0).total_seconds() / 60


def run_coin(sym):
    daily, hourly, m5, start = fetch(sym)
    trades, last = [], None
    for i, hb in enumerate(hourly):
        t = hb["t"] + timedelta(hours=1)  # tidspunkt hvor timen er slut
        if t < start or i < 100 or t > hourly[-1]["t"]:
            continue
        if last and t - last < timedelta(hours=GAP_H):
            continue
        h = hourly[:i + 1]
        d = [x for x in daily if x["t"] + timedelta(days=1) <= t]
        mm = [x for x in m5 if x["t"] < t][-300:]
        if len(d) < 30 or len(mm) < 20:
            continue
        price = h[-1]["c"]
        try:
            f = fx.compute_features(d, h, mm, price)
        except Exception:
            continue
        room = (f["naeste_modstand"] - price) / price
        if not f["setups"] or f["trend_1d"] < 0 or room < bot.CRYPTO_MIN_ROOM_PCT \
                or f["afstand_modstand_atr"] < bot.STOP_ATR_MULT:
            continue
        atr = f["atr_1h"]
        stop = price * (1 - bot.stop_frac(sym, price, atr))
        res = {}
        for name, be_at, ts_ in (("gammel", bot.BREAKEVEN_AT_R, 0), ("ny", 0.5, TIME_STOP_MIN)):
            px, why, mins = simulate(price, stop, atr, t, m5, be_at, ts_)
            pct = (px / price - 1) - 2 * FEE
            res[name] = (px, why, mins, pct)
        trades.append({"coin": sym, "tid": t, "entry": price, "stop": stop,
                       "setup": "+".join(f["setups"]), **res})
        last = t
    return trades


def fmt_usd(x):
    return f"{x:+.0f} $"


def main():
    allt = []
    for sym in bot.CRYPTO:
        try:
            tr = run_coin(sym)
            print(f"{sym}: {len(tr)} koeb")
            allt += tr
        except Exception as e:
            print(f"{sym} fejlede: {type(e).__name__}: {e}")
    allt.sort(key=lambda x: x["tid"])
    last10 = allt[-SHOW:]

    def total(ts, k):
        return sum(x[k][3] for x in ts) * TRADE_USD

    def wins(ts, k):
        return sum(1 for x in ts if x[k][3] > 0)

    L = ["# Krypto-test af salgsreglerne", "",
         f"Kurser fra de sidste {DAYS} dage{' (FALSKE testdata)' if TEST else ''}. "
         f"Hver handel er regnet som {TRADE_USD} $. Gebyret (0,5 % i alt) er trukket fra.",
         "AI'en er ikke med. Botten koeber kun, hvor de faste regler siger ja, saa det er salget, der testes.", ""]
    if not allt:
        L.append("Ingen koeb i perioden: reglerne (dagstrend op og 3 % plads) sagde nej hele tiden.")
    else:
        L += ["## De 10 nyeste handler", "",
              "| Tid (UTC) | Coin | Koebt | Gamle regler | Nye regler |", "|---|---|---|---|---|"]
        for x in last10:
            g, n = x["gammel"], x["ny"]
            L.append(f"| {x['tid']:%d/%m %H:%M} | {x['coin'].split('/')[0]} | {x['entry']:.2f} | "
                     f"{fmt_usd(g[3] * TRADE_USD)} ({g[1]}, {g[2] / 60:.0f} t) | "
                     f"{fmt_usd(n[3] * TRADE_USD)} ({n[1]}, {n[2] / 60:.1f} t) |")
        L += ["", "## Resultat", "",
              "| | Gamle regler | Nye regler |", "|---|---|---|",
              f"| De 10 nyeste | {fmt_usd(total(last10, 'gammel'))} ({wins(last10, 'gammel')} vundet) | "
              f"{fmt_usd(total(last10, 'ny'))} ({wins(last10, 'ny')} vundet) |",
              f"| Alle {len(allt)} handler | {fmt_usd(total(allt, 'gammel'))} ({wins(allt, 'gammel')} vundet) | "
              f"{fmt_usd(total(allt, 'ny'))} ({wins(allt, 'ny')} vundet) |", ""]
    out = "backtest_resultat.md"
    with open(out, "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
