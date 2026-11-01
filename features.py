"""
Deterministiske features til tradingbotten (ren Python, intet netvaerk, ingen AI).

Input: lister af bars som dicts {"t": datetime, "o","h","l","c","v": float}, aeldste foerst.
compute_features() returnerer et fladt dict, som baade AI-rollerne og koden bruger.
"""
import math
from statistics import median


# ---------------- Basale indikatorer ----------------
def ema(xs, n):
    k, out, e = 2 / (n + 1), [], xs[0]
    for x in xs:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    if len(closes) <= n:
        return 50.0
    gains = [max(b - a, 0) for a, b in zip(closes[:-1], closes[1:])]
    losses = [max(a - b, 0) for a, b in zip(closes[:-1], closes[1:])]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):  # Wilder-udglatning
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def true_ranges(bars):
    trs = [bars[0]["h"] - bars[0]["l"]]
    for p, b in zip(bars[:-1], bars[1:]):
        trs.append(max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])))
    return trs


def atr_series(bars, n=14):
    trs = true_ranges(bars)
    if len(trs) < n:
        return [sum(trs) / len(trs)] * len(trs)
    a = sum(trs[:n]) / n
    out = [a] * n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
        out.append(a)
    return out


def adx(bars, n=14):
    """Trendstyrke 0-100 (Wilder). Over ca. 25 = der er en trend."""
    if len(bars) < 2 * n + 1:
        return 20.0
    pdm, ndm = [], []
    for p, b in zip(bars[:-1], bars[1:]):
        up, down = b["h"] - p["h"], p["l"] - b["l"]
        pdm.append(up if up > down and up > 0 else 0.0)
        ndm.append(down if down > up and down > 0 else 0.0)
    trs = true_ranges(bars)[1:]
    tr_s, p_s, n_s = sum(trs[:n]), sum(pdm[:n]), sum(ndm[:n])
    dxs = []
    for tr, p, m in zip(trs[n:], pdm[n:], ndm[n:]):
        tr_s, p_s, n_s = tr_s - tr_s / n + tr, p_s - p_s / n + p, n_s - n_s / n + m
        pdi, ndi = 100 * p_s / max(tr_s, 1e-9), 100 * n_s / max(tr_s, 1e-9)
        dxs.append(100 * abs(pdi - ndi) / max(pdi + ndi, 1e-9))
    a = sum(dxs[:n]) / n
    for d in dxs[n:]:
        a = (a * (n - 1) + d) / n
    return a


def efficiency_ratio(closes, n):
    """Kaufman: 1 = ren retning, 0 = ren stoej."""
    n = min(n, len(closes) - 1)
    if n < 2:
        return 0.0
    path = sum(abs(b - a) for a, b in zip(closes[-n - 1:-1], closes[-n:]))
    return abs(closes[-1] - closes[-n - 1]) / path if path else 0.0


def percentile_rank(series, value):
    if not series:
        return 50.0
    return 100.0 * sum(1 for x in series if x <= value) / len(series)


def resample(bars, hours):
    """1t-bars til N-timers bars, justeret til UTC (fx 4t = 00,04,08 ...)."""
    out, key = [], None
    for b in bars:
        k = int(b["t"].timestamp()) // (hours * 3600)
        if k != key:
            out.append(dict(b))
            key = k
        else:
            o = out[-1]
            o["h"], o["l"], o["c"], o["v"] = max(o["h"], b["h"]), min(o["l"], b["l"]), b["c"], o["v"] + b["v"]
    return out


def swing_levels(bars, k=2):
    """Fraktal-pivots: et high/low der er hoejere/lavere end k bars paa hver side."""
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if bars[i]["h"] == max(b["h"] for b in win):
            highs.append(bars[i]["h"])
        if bars[i]["l"] == min(b["l"] for b in win):
            lows.append(bars[i]["l"])
    return highs, lows


def trend_state(closes):
    """+1 op, -1 ned, 0 uklar. Pris vs EMA20 vs EMA50 + haeldning paa EMA20."""
    if len(closes) < 55:
        return 0
    e20, e50 = ema(closes, 20), ema(closes, 50)
    slope = e20[-1] - e20[-6]
    if closes[-1] > e20[-1] > e50[-1] and slope > 0:
        return 1
    if closes[-1] < e20[-1] < e50[-1] and slope < 0:
        return -1
    return 0


# ---------------- Samlet feature-saet ----------------
def compute_features(daily, hourly, m5, price):
    h_close = [b["c"] for b in hourly]
    h_close[-1] = price
    h4 = resample(hourly, 4)
    h4_close = [b["c"] for b in h4]
    d_close = [b["c"] for b in daily] + [price]

    atr_h = atr_series(hourly)
    atr = atr_h[-1]
    atr_pct = atr / price * 100
    atr_pctile = percentile_rank([a / c for a, c in zip(atr_h[-720:], h_close[-720:])], atr / price)
    rets = [math.log(b / a) for a, b in zip(h_close[-25:-1], h_close[-24:])]
    mean = sum(rets) / len(rets)
    rv_24h = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * math.sqrt(24 * 365) * 100

    vols_h = [b["v"] for b in hourly]
    rvol_1h = vols_h[-1] / max(median(vols_h[-25:-1]), 1e-9)
    vols_5 = [b["v"] for b in m5]
    rvol_5m = (sum(vols_5[-3:]) / 3) / max(median(vols_5[-289:-3] or [1]), 1e-9) if len(vols_5) > 6 else 1.0

    hi24, lo24 = max(b["h"] for b in hourly[-24:]), min(b["l"] for b in hourly[-24:])
    sh, sl = swing_levels(hourly[-72:])
    res = min([h for h in sh if h > price], default=hi24 if hi24 > price else price + 3 * atr)
    sup = max([l for l in sl if l < price], default=lo24 if lo24 < price else price - 3 * atr)

    e20h = ema(h_close, 20)[-1]
    adx_h = adx(hourly[-120:])
    er_24 = efficiency_ratio(h_close, 24)
    t1h, t4h, t1d = trend_state(h_close), trend_state(h4_close), trend_state(d_close)
    up_bars = sum(1 for a, b in zip(h_close[-13:-1], h_close[-12:]) if b > a)

    if atr_pctile >= 85:
        regime = "high_vol"
    elif adx_h >= 25 and er_24 >= 0.3 and t4h != 0:
        regime = "trend_op" if t4h > 0 else "trend_ned"
    else:
        regime = "range"

    f = {
        "pris": round(price, 2),
        "trend_1h": t1h, "trend_4h": t4h, "trend_1d": t1d,
        "regime": regime,
        "atr_1h": round(atr, 2), "atr_pct": round(atr_pct, 3), "atr_percentil_30d": round(atr_pctile, 1),
        "realiseret_vol_24h_pct": round(rv_24h, 1),
        "rel_volumen_1h": round(rvol_1h, 2), "rel_volumen_15m": round(rvol_5m, 2),
        "high_24h": round(hi24, 2), "low_24h": round(lo24, 2),
        "afstand_high_24h_atr": round((hi24 - price) / atr, 2),
        "afstand_low_24h_atr": round((price - lo24) / atr, 2),
        "naeste_modstand": round(res, 2), "naeste_stoette": round(sup, 2),
        "afstand_modstand_atr": round((res - price) / atr, 2),
        "afstand_stoette_atr": round((price - sup) / atr, 2),
        "afstand_ema20_1h_atr": round((price - e20h) / atr, 2),
        "rsi_1h": round(rsi(h_close), 1), "rsi_4h": round(rsi(h4_close), 1),
        "adx_1h": round(adx_h, 1), "efficiency_24h": round(er_24, 2),
        "roc_4h_pct": round((price / h_close[-5] - 1) * 100, 2),
        "roc_24h_pct": round((price / h_close[-25] - 1) * 100, 2),
        "roc_7d_pct": round((price / d_close[-8] - 1) * 100, 2) if len(d_close) > 8 else 0.0,
        "op_bars_sidste_12h": up_bars,
    }
    f["setups"] = detect_setups(f)
    return f


def detect_setups(f):
    """Billige, faste regler der afgoer om AI-teamet overhovedet skal spoerges om et koeb.
    v2.1: loesnet, saa AI'en faar flere muligheder at vurdere (Chef/Skeptiker/Risk filtrerer bagefter)."""
    s = []
    if (f["trend_4h"] >= 0 and f["trend_1d"] >= 0 and f["trend_1h"] >= 0 and f["regime"] != "high_vol"
            and (f["trend_4h"] == 1 or f["trend_1h"] == 1)
            and -1.0 <= f["afstand_ema20_1h_atr"] <= 1.5 and 35 <= f["rsi_1h"] <= 65):
        s.append("trend_pullback")
    if (f["afstand_high_24h_atr"] <= 0.3 and f["rel_volumen_1h"] >= 1.2 and f["trend_4h"] >= 0
            and f["rsi_1h"] < 75):
        s.append("breakout")
    if f["afstand_low_24h_atr"] <= 1.0 and f["rsi_1h"] <= 42 and f["trend_1d"] >= 0 and f["regime"] != "trend_ned":
        s.append("range_bund")
    return s


def summary_text(f):
    """Kort, fast tekst til AI-rollerne."""
    tr = {1: "op", 0: "flad", -1: "ned"}
    return (f"Pris {f['pris']}. Regime: {f['regime']}. Trend 1h/4h/1d: {tr[f['trend_1h']]}/"
            f"{tr[f['trend_4h']]}/{tr[f['trend_1d']]}. ADX1h {f['adx_1h']}, efficiency24h {f['efficiency_24h']}. "
            f"ATR1h {f['atr_1h']} ({f['atr_pct']}%, percentil {f['atr_percentil_30d']}), realiseret vol "
            f"{f['realiseret_vol_24h_pct']}%/aar. RSI 1h/4h {f['rsi_1h']}/{f['rsi_4h']}. ROC 4h/24h/7d "
            f"{f['roc_4h_pct']}/{f['roc_24h_pct']}/{f['roc_7d_pct']}%. Rel. volumen 1h {f['rel_volumen_1h']}x, "
            f"15m {f['rel_volumen_15m']}x. 24h high/low {f['high_24h']}/{f['low_24h']} "
            f"({f['afstand_high_24h_atr']}/{f['afstand_low_24h_atr']} ATR vaek). Naeste modstand "
            f"{f['naeste_modstand']} ({f['afstand_modstand_atr']} ATR), stoette {f['naeste_stoette']} "
            f"({f['afstand_stoette_atr']} ATR). Afstand til EMA20 1h: {f['afstand_ema20_1h_atr']} ATR. "
            f"Setups fundet af reglerne: {', '.join(f['setups']) or 'ingen'}.")
