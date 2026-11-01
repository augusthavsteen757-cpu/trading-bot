# Tradingbot v2 (Alpaca PAPER, kun BTC/USD)

Kun falske penge. Koerer hvert 5. minut i GitHub Actions (et langt job, der starter sig selv igen).

## Saadan virker en runde
1. **Data og features (gratis, ingen AI)** i `features.py`: trend paa 1h/4h/1d, ATR og volatilitets-percentil,
   realiseret vol, relativ volumen (1h og 15m), afstand til 24h high/low og swing-niveauer i ATR,
   momentum-kvalitet (ADX, efficiency ratio, ROC, RSI) og regime (`trend_op`, `trend_ned`, `range`, `high_vol`).
2. **Faste regler finder setups**: `trend_pullback`, `breakout`, `range_bund`. Intet setup = ingen AI-kald.
3. **Positionsstyring i kode**: stop-ordre hos Alpaca, stop flyttes til break-even ved +1R og trailer
   2,5 x ATR under toppen, software-stop som sikkerhedsnet, manglende stop-ordre genskabes.
4. **Kill-switch i kode**: -2 % paa dagen = ingen nye koeb i dag. -8 % fra top = HALT indtil `RESET_HALT=1`.
5. **AI-teamet** (kun ved setup, max 10 gange/doegn, min. 15 min imellem, eller hver time ved aaben position):

| Rolle | Model | Opgave |
|---|---|---|
| Scout, Nyheder, Stemning, Chart | Haiku | Signal + styrke 1-3 ud fra hver sin del af data |
| Chef | Sonnet | KOEB/SAELG/HOLD, conviction 1-5, setup, hvad der beviser beslutningen forkert |
| Skeptiker | Sonnet | Tjekliste med 7 punkter + faste regel-advarsler. Veto ved eget veto, 3 flag eller 2 regel-advarsler |
| Risk Manager | Sonnet | Faar den beregnede stoerrelse. Kan godkende, reducere (0-1) eller blokere. Kan aldrig oege |
| Analytiker | Sonnet | Lektie i `lessons.md` efter 2 lignende tilfaelde: [-] tab, [+] gevinst, teknisk fejl |

Chef, Skeptiker og Risk Manager faar ogsaa performance for de sidste 30 handler (winrate, gns. R, per setup/regime)
og de nyeste lektier (5 negative, 3 positive).

## Stoerrelse og stop
- Stop = entry - 2 x ATR(1t), mellem 1 % og 6 %.
- Risiko = 0,5 % af kontoen x conviction (1: 25 % ... 5: 100 %) x 0,5 i `high_vol`.
- Stoerrelse = risiko / stop-afstand, haardt loft 15 % af kontoen, derefter x Risk Managerens faktor.

## Filer
- `trades.csv`: en linje per runde (til mennesker). Den gamle v1-fil gemmes som `trades_v1.csv`.
- `closed_trades.csv`: en linje per lukket handel med R-multiple (bruges til performance og laering).
- `logs/decisions-AAAA-MM.jsonl`: alle features og alle AI-svar per runde (til backtest senere).
- `state.json`: position, budget, kill-switch. Skrives atomisk.

## Indstillinger
Se toppen af `bot.py` og `.env.example`. Paa GitHub saettes de som Actions-variabler (ikke secrets).

## AI-forbrug
Haiku-runde ca. 0,0004 USD, fuld koebs-kaede med Sonnet ca. 0,015 USD. Med max 10 AI-runder/doegn er
loftet ca. 5 USD/md. Botten stopper selv ved `MONTHLY_BUDGET_USD` (7,50) og fordeler forbruget over maaneden.

## Test sikkert
    pip install -r requirements.txt
    python bot.py --test   # falske data, falsk AI, ingen noegler, skriver test_*-filer
    python bot.py --dry    # rigtige data og rigtig AI, men ingen ordrer, skriver dry_*-filer
Paper-kontoen er i sig selv sikker: `paper=True` er hardcodet.

## Koer paa GitHub
Secrets: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ANTHROPIC_API_KEY`. Repoet skal vaere offentligt for gratis minutter.
Stop botten: Actions > koersel > Cancel (den starter ikke sig selv igen efter Cancel).
