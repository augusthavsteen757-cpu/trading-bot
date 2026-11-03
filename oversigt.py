"""
Laver OVERSIGT.md: alle bots paa en side (ingen API-kald, laeser kun filerne i repoet).
Koeres af hoved-botten (bot.yml) efter hver runde, saa kun en bot skriver filen.
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
DK = ZoneInfo("Europe/Copenhagen")
BOTS = [  # (navn, mappe, beskrivelse)
    ("Hoved-botten", BASE, "store aktier + BTC/ETH/SOL"),
    ("Small-botten", BASE / "small", "10 små aktier"),
    ("Krypto-botten", BASE / "crypto", "7 coins, døgnet rundt"),
    ("Options-botten", BASE / "options", "calls/puts på 10 store aktier"),
    ("Aggressiv-botten", BASE / "aggressiv", "færre, større aktiehandler"),
]


def rows(path):
    try:
        with open(path, newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def state(path):
    try:
        return json.loads((path / "state.json").read_text())
    except Exception:
        return {}


def dk(t):
    """'2026-11-03 14:39' (UTC) eller ISO-tid -> '3/11 15.39' dansk tid."""
    if not t:
        return ""
    try:
        d = datetime.fromisoformat(t.replace(" ", "T"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(DK).strftime("%-d/%-m %H.%M")
    except ValueError:
        return t


def pretty(sym):
    """'SPY261120P00757000' -> 'SPY put 757 (udl. 20/11)'. Andet uaendret."""
    if sym and len(sym) > 15 and sym[-9] in "CP" and sym[-15:].replace("C", "").replace("P", "").isdigit():
        t = sym[-15:]
        return f"{sym[:-15]} {'call' if t[6] == 'C' else 'put'} {int(t[7:]) / 1000:g} (udl. {int(t[4:6])}/{int(t[2:4])})"
    return sym


def num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def kr(x):
    return f"{x:+,.0f} USD".replace(",", ".")


def usd(x):
    return f"{x:,.0f} USD".replace(",", ".")


def bot_section(name, d, desc):
    tr = rows(d / "trades.csv")
    if not tr:
        return [f"## {name} ({desc})", "", "Ikke startet endnu.", ""]
    last = tr[-1]
    eq = num(last.get("konto_vaerdi"), 100000)
    day = num(last.get("dagens_pnl_pct"))
    st = state(d)
    start = num(st.get("start_equity"), 100000) or 100000
    total = eq - start
    icon = "🟢" if total >= 0 else "🔴"
    L = [f"## {icon} {name} ({desc})", "",
         f"**Konto:** {usd(eq)}  ·  **I alt:** {kr(total)} ({total / start * 100:+.2f} %)  ·  "
         f"**I dag:** {day:+.2f} %  ·  sidst opdateret {dk(last.get('tid_utc'))}", ""]
    if st.get("auto_tjek"):  # aggressiv-botten: virker den aggressive stil?
        L += [f"**Selv-tjek:** {st['auto_tjek']}", ""]
    # aabne handler
    if "positions" in st:  # options-botten
        pos = [(k, v.get("antal"), v.get("entry"), dk(v.get("entry_tid"))) for k, v in st["positions"].items()]
    else:
        pos = [(k, v["pos"].get("qty"), v["pos"].get("entry"), dk(v["pos"].get("entry_tid")))
               for k, v in (st.get("coins") or {}).items() if v.get("pos")]
    if pos:
        L += ["**Ejer nu:**", "", "| Hvad | Antal | Købt til | Købt |", "|---|---|---|---|"]
        L += [f"| {pretty(k)} | {q:g} | {num(e):.2f} | {t} |" for k, q, e, t in pos]
    else:
        L.append("**Ejer nu:** intet")
    closed = rows(d / "closed_trades.csv")
    if closed:
        won = sum(1 for c in closed if num(c.get("pnl_usd")) > 0)
        pnl = sum(num(c.get("pnl_usd")) for c in closed)
        L += ["", f"**Lukkede handler:** {len(closed)} i alt, {won} med gevinst, samlet {kr(pnl)}", "",
              "| Hvad | Solgt | Resultat | Hvorfor solgt |", "|---|---|---|---|"]
        for c in closed[-5:][::-1]:
            what = c.get("coin") or c.get("kontrakt") or c.get("aktie")
            p = num(c.get("pnl_usd"))
            L.append(f"| {'✅' if p > 0 else '❌'} {pretty(what)} | {dk(c.get('exit_tid'))} | {kr(p)} | {c.get('exit_aarsag', '')} |")
    L.append("")
    return L


def main():
    now = datetime.now(DK).strftime("%-d/%-m kl. %H.%M")
    L = ["# Oversigt over alle bots", "",
         f"Opdateres af sig selv hvert 5. minut. Sidst: **{now}** (dansk tid). Kun falske penge (paper).", ""]
    for name, d, desc in BOTS:
        L += bot_section(name, d, desc)
    (BASE / "OVERSIGT.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
