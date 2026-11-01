"""
Multi-agent tradingbot til Alpaca PAPER (kun falske penge), kun BTC/USD.

6 roller (claude-haiku-5-5): Scout, Nyheder, Stemning, Chart, Skeptiker (veto), Chef.
Koer: python bot.py          (en runde, rigtig paper-handel)
      python bot.py --test   (en runde med falske data, ingen noegler, ingen handel)
"""
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------- Indstillinger ----------------
SYMBOL = "BTC/USD"
POS_SYMBOL = "BTCUSD"
MODEL = "claude-haiku-5-5"
MAX_POSITION_PCT = 0.10      # max 10 % af kontoen per handel
STOP_LOSS_PCT = 0.03         # stop-loss 3 % under koebspris
MONTHLY_BUDGET_USD = 7.50    # stop AI-kald foer 8 USD-graensen
PRICE_IN = 0.10 / 1_000_000  # USD per input-token (Haiku 5.5)
PRICE_OUT = 0.50 / 1_000_000 # USD per output-token

BASE = Path(__file__).parent
TEST_MODE = "--test" in sys.argv
TRADES_CSV = BASE / ("test_trades.csv" if TEST_MODE else "trades.csv")
STATE_FILE = BASE / ("test_state.json" if TEST_MODE else "state.json")
load_dotenv(BASE / ".env")

CSV_FIELDS = [
    "tid_utc", "pris", "scout", "nyheder", "stemning", "chart",
    "chef_beslutning", "chef_grund", "skeptiker_veto", "skeptiker_grund",
    "handling", "antal_btc", "stop_loss", "konto_vaerdi", "bot_afkast_pct",
    "koeb_og_behold_vaerdi", "koeb_og_behold_afkast_pct", "api_forbrug_usd_md",
]


# ---------------- Hjaelpere ----------------
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def log_row(row):
    new = not TRADES_CSV.exists()
    with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def rsi(closes, n=14):
    if len(closes) <= n:
        return 50.0
    gains, losses = [], []
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g, avg_l = sum(gains) / n, sum(losses) / n
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def sma(closes, n):
    n = min(n, len(closes))
    return sum(closes[-n:]) / n


# ---------------- Data ----------------
def get_market_data():
    """Returnerer liste af timelukkepriser (sidste ~3 doegn) og volumen."""
    if TEST_MODE:
        import random
        p, closes = 68000.0, []
        for _ in range(72):
            p *= 1 + random.uniform(-0.01, 0.01)
            closes.append(round(p, 2))
        return closes, [100.0] * 72
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = CryptoHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    req = CryptoBarsRequest(symbol_or_symbols=SYMBOL, timeframe=TimeFrame.Hour,
                            start=datetime.now(timezone.utc) - timedelta(hours=72))
    bars = client.get_crypto_bars(req).data[SYMBOL]
    return [b.close for b in bars], [b.volume for b in bars]


def get_news():
    if TEST_MODE:
        return ["Bitcoin ETF har stor indstroemning", "Fed holder renten uaendret"]
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest
        client = NewsClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
        res = client.get_news(NewsRequest(symbols="BTCUSD", limit=8,
                                          start=datetime.now(timezone.utc) - timedelta(hours=24)))
        return [n.headline for n in res.data.get("news", [])][:8] or ["Ingen nyheder sidste doegn"]
    except Exception as e:
        return [f"Nyheder utilgaengelige ({type(e).__name__})"]


def get_fear_greed():
    if TEST_MODE:
        return "55 (Greed)"
    try:
        d = requests.get("https://api.alternative.me/fng/?limit=3", timeout=10).json()["data"]
        return ", ".join(f"{x['value']} ({x['value_classification']})" for x in d) + " (nyeste foerst)"
    except Exception:
        return "ukendt"


# ---------------- AI-roller ----------------
class Brain:
    def __init__(self, state):
        self.state = state
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        if state.get("budget_month") != month:
            state["budget_month"], state["budget_used"] = month, 0.0
        self.client = None
        if not TEST_MODE:
            import anthropic
            self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def over_budget(self):
        return self.state["budget_used"] >= MONTHLY_BUDGET_USD

    def ask(self, role, instructions, data, schema):
        if TEST_MODE:
            fake = {"vurdering": f"{role}: testvurdering", "signal": "neutral",
                    "beslutning": "KOEB", "grund": "test", "veto": False}
            return {k: fake[k] for k in schema["properties"]}
        resp = self.client.messages.create(
            model=MODEL,
            max_tokens=400,
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            system=(f"Du er '{role}' i et team, der handler BTC/USD hver time paa en paper-konto. "
                    f"{instructions} Svar kort paa dansk (max 25 ord per felt)."),
            messages=[{"role": "user", "content": data}],
        )
        u = resp.usage
        self.state["budget_used"] += u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"{role} afviste forespoergslen")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)


def analyst_schema():
    return {"type": "object",
            "properties": {"signal": {"type": "string", "enum": ["bullish", "neutral", "bearish"]},
                           "vurdering": {"type": "string"}},
            "required": ["signal", "vurdering"], "additionalProperties": False}


def run_team(brain, closes, volumes, news, fng, has_position):
    price = closes[-1]
    stats = (f"Pris nu: {price:.0f}. Aendring 1t: {(price / closes[-2] - 1) * 100:.2f}%, "
             f"24t: {(price / closes[-25] - 1) * 100:.2f}%, 72t: {(price / closes[0] - 1) * 100:.2f}%. "
             f"Hoej/lav 24t: {max(closes[-24:]):.0f}/{min(closes[-24:]):.0f}. "
             f"Volumen sidste time vs 24t-snit: {volumes[-1] / max(sma(volumes, 24), 1e-9):.2f}x.")
    chart = (f"Pris {price:.0f}, SMA12 {sma(closes, 12):.0f}, SMA48 {sma(closes, 48):.0f}, "
             f"RSI14 {rsi(closes):.0f}. Sidste 12 timers lukninger: {[round(c) for c in closes[-12:]]}")

    r = {}
    r["scout"] = brain.ask("Scout", "Du giver et hurtigt overblik over markedet lige nu.", stats, analyst_schema())
    r["nyheder"] = brain.ask("Nyheder", "Du vurderer om nyhederne er gode eller daarlige for BTC paa kort sigt.",
                             "Overskrifter: " + " | ".join(news), analyst_schema())
    r["stemning"] = brain.ask("Stemning", "Du vurderer markedsstemningen ud fra Fear & Greed-indekset.",
                              f"Fear & Greed: {fng}", analyst_schema())
    r["chart"] = brain.ask("Chart", "Du er teknisk analytiker og laeser trend og RSI.", chart, analyst_schema())

    summary = "\n".join(f"{k}: {v['signal']} - {v['vurdering']}" for k, v in r.items())
    pos_txt = "Vi EJER BTC lige nu." if has_position else "Vi ejer IKKE BTC lige nu."

    chef = brain.ask(
        "Chef",
        "Du traeffer den endelige beslutning. KOEB kun hvis vi ikke ejer BTC og der er klar fordel. "
        "SAELG kun hvis vi ejer BTC. Ellers HOLD. Vaer forsigtig: ingen handel er bedre end en daarlig handel.",
        f"{pos_txt}\nTeamets vurderinger:\n{summary}",
        {"type": "object",
         "properties": {"beslutning": {"type": "string", "enum": ["KOEB", "SAELG", "HOLD"]},
                        "grund": {"type": "string"}},
         "required": ["beslutning", "grund"], "additionalProperties": False})

    skeptic = {"veto": False, "grund": "Ingen handel at vurdere"}
    if chef["beslutning"] == "KOEB":
        skeptic = brain.ask(
            "Skeptiker",
            "Din opgave er at finde fejl i Chefens koebsbeslutning. Brug veto hvis signalerne er "
            "modstridende, hvis RSI er over 70, eller hvis risikoen er hoejere end gevinsten.",
            f"Chefen vil KOEBE fordi: {chef['grund']}\nTeamets vurderinger:\n{summary}\n{chart}",
            {"type": "object",
             "properties": {"veto": {"type": "boolean"}, "grund": {"type": "string"}},
             "required": ["veto", "grund"], "additionalProperties": False})
    return r, chef, skeptic


# ---------------- Handel ----------------
class Broker:
    def __init__(self):
        if TEST_MODE:
            self.fake = {"equity": 100000.0, "qty": 0.0}
            return
        from alpaca.trading.client import TradingClient
        self.t = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)

    def equity(self):
        return self.fake["equity"] if TEST_MODE else float(self.t.get_account().equity)

    def position_qty(self):
        if TEST_MODE:
            return self.fake["qty"]
        try:
            return float(self.t.get_open_position(POS_SYMBOL).qty)
        except Exception:
            return 0.0

    def cancel_open_orders(self):
        if TEST_MODE:
            return
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        for o in self.t.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[SYMBOL])):
            self.t.cancel_order_by_id(o.id)

    def buy(self, usd, price):
        """Markedskoeb for 'usd' dollars + stop-limit salgsordre som stop-loss."""
        stop = round(price * (1 - STOP_LOSS_PCT), 2)
        if TEST_MODE:
            self.fake["qty"] = usd / price
            return self.fake["qty"], stop
        import time
        from alpaca.trading.requests import MarketOrderRequest, StopLimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        self.t.submit_order(MarketOrderRequest(symbol=SYMBOL, notional=round(usd, 2),
                                               side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
        qty = 0.0
        for _ in range(10):
            time.sleep(2)
            qty = self.position_qty()
            if qty > 0:
                break
        if qty > 0:
            self.t.submit_order(StopLimitOrderRequest(
                symbol=SYMBOL, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.GTC,
                stop_price=stop, limit_price=round(stop * 0.995, 2)))
        return qty, stop

    def sell_all(self):
        qty = self.position_qty()
        if TEST_MODE:
            self.fake["qty"] = 0.0
            return qty
        self.cancel_open_orders()
        self.t.close_position(POS_SYMBOL)
        return qty


# ---------------- Hovedloekke ----------------
def main():
    state = load_state()
    broker = Broker()
    closes, volumes = get_market_data()
    price = closes[-1]
    equity = broker.equity()

    # Startpunkt til koeb-og-behold-sammenligning
    if "start_equity" not in state:
        state.update(start_equity=equity, start_price=price,
                     start_time=datetime.now(timezone.utc).isoformat())
    bh_value = state["start_equity"] * price / state["start_price"]

    qty = broker.position_qty()
    row = {"tid_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), "pris": round(price, 2)}

    # Sikkerhedsnet: software stop-loss hvis stop-ordren ikke ramte
    stop_price = state.get("stop_price")
    if qty > 0 and stop_price and price <= stop_price:
        sold = broker.sell_all()
        state.pop("stop_price", None)
        row.update(handling="STOP-LOSS SALG", antal_btc=sold, chef_grund="Pris under stop-loss")
        finish(row, state, broker, bh_value)
        return
    if qty == 0:
        state.pop("stop_price", None)

    brain = Brain(state)
    if brain.over_budget():
        row.update(handling="HOLD (API-budget brugt)", chef_beslutning="HOLD")
        finish(row, state, broker, bh_value)
        return

    try:
        r, chef, skeptic = run_team(brain, closes, volumes, get_news(), get_fear_greed(), qty > 0)
    except Exception as e:
        row.update(handling=f"HOLD (fejl: {type(e).__name__})", chef_beslutning="HOLD", chef_grund=str(e)[:200])
        finish(row, state, broker, bh_value)
        return

    for k in ("scout", "nyheder", "stemning", "chart"):
        row[k] = f"{r[k]['signal']}: {r[k]['vurdering']}"
    row.update(chef_beslutning=chef["beslutning"], chef_grund=chef["grund"],
               skeptiker_veto=skeptic["veto"], skeptiker_grund=skeptic["grund"])

    action = "HOLD"
    if chef["beslutning"] == "KOEB" and qty == 0:
        if skeptic["veto"]:
            action = "INGEN HANDEL (veto)"
        else:
            bought, stop = broker.buy(equity * MAX_POSITION_PCT, price)
            state["stop_price"] = stop
            action = "KOEB"
            row.update(antal_btc=round(bought, 6), stop_loss=stop)
    elif chef["beslutning"] == "SAELG" and qty > 0:
        sold = broker.sell_all()
        state.pop("stop_price", None)
        action = "SAELG"
        row.update(antal_btc=round(sold, 6))
    row["handling"] = action
    if action == "HOLD" and state.get("stop_price"):
        row["stop_loss"] = state["stop_price"]
    finish(row, state, broker, bh_value)


def finish(row, state, broker, bh_value):
    eq = broker.equity()
    row.update(
        konto_vaerdi=round(eq, 2),
        bot_afkast_pct=round((eq / state["start_equity"] - 1) * 100, 3),
        koeb_og_behold_vaerdi=round(bh_value, 2),
        koeb_og_behold_afkast_pct=round((bh_value / state["start_equity"] - 1) * 100, 3),
        api_forbrug_usd_md=round(state.get("budget_used", 0.0), 4),
    )
    log_row(row)
    save_state(state)
    print(json.dumps(row, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
