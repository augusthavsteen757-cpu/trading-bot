# Tradingbot v2 (Alpaca PAPER, BTC, ETH og SOL)

**Se alle 4 bots paa en side: [OVERSIGT.md](OVERSIGT.md)** (opdateres hvert 5. minut).

**Krypto-bot (fra 3. nov 2026, bot nr. 3):** samme strategi og regler som hoved-botten, men KUN krypto (BTC, ETH, SOL, DOGE, AVAX, LINK, LTC) doegnet rundt paa sin EGEN Alpaca paper-konto. Workflow `bot_crypto.yml`, data i mappen `crypto/`. Kraever secrets `ALPACA_CRYPTO_KEY` og `ALPACA_CRYPTO_SECRET`; uden dem venter den. AI-budget 2,50 USD/md og max 20 AI-runder/doegn (Actions-var `CRYPTO_MONTHLY_BUDGET_USD`), saa alle bots tilsammen bruger ca. 10 USD/md. Coins aendres med `CRYPTO_COINS`.

**Options-bot (fra 3. nov 2026, bot nr. 4, `options_bot.py`):** ren regel-bot uden AI (0 kr). For hver af de 10 store aktier taelles, hvor stor en del af dagens optionshandel der er calls (vaedder paa stigning) mod puts (vaedder paa fald), med gratis data fra Alpaca. Koeb kun naar hint OG trend er enige: calls >= 65 % og aktien over 20-dages snit = koeb en call; puts >= 60 % og aktien under snit = koeb en put (loesnet 3. nov). Engangs-testkoeb af 1 kontrakt: ny tekst i `options/koeb_test.flag`. Kontrakt 14-45 dage ud, delta 0,4-0,6, smal spread. Max 1 % af kontoen pr. handel (det er hele det mulige tab), max 4 aabne, 3 nye pr. dag. Saelg ved +50 %, -40 %, 7 dage foer udloeb, efter 14 dage, eller naar trenden vender. Kill-switch -3 %/dag eller -10 % fra top. Workflow `bot_options.yml`, data i `options/` (trades.csv, closed_trades.csv). Kraever secrets `ALPACA_OPTIONS_KEY` og `ALPACA_OPTIONS_SECRET`.

**Small-bot (fra 3. nov 2026, bot nr. 2):** samme strategi og regler, men 10 smaa, volatile aktier paa sin EGEN Alpaca paper-konto, saa den faar sin egen graf: ONDS, RCAT, UMAC, BBAI, SOUN, QUBT, RGTI, LUNR, ACHR, OUST (droner, AI, kvante, rumfart, lidar). Workflow `bot_small.yml`, data i mappen `small/` (trades.csv, closed_trades.csv, state.json, logs). Kraever secrets `ALPACA_SMALL_KEY` og `ALPACA_SMALL_SECRET`; uden dem venter den bare. AI-budget 2,50 USD/md (hoved-botten 5,00), i alt 7,50. Koeb-og-behold sammenlignes med ONDS. Aendr listen med Actions-variablen `SMALL_STOCKS`.

**ORB (opening range breakout, SPY og QQQ):** ren regel uden AI. Range = 9:30-9:45 New York (15:30-15:45 dansk vintertid). Koeb naar en 5m-bar lukker over toppen (9:45-11:30), stop i midten, maal 2R, max en handel pr. dag, altid ud foer 15:50. Setup hedder `orb` i closed_trades.csv.

**Nyhedsvagt (newsguard.py):** foer et nyt koeb tjekkes regnskab (i gaar/i dag/i morgen, Nasdaq) og store negative nyheder sidste 12 t (Yahoo Finance, CoinDesk, Cointelegraph). Rammer det, koebes der ikke. Fejler en kilde, blokeres intet.

**Nyhedsjaeger (fra 2. nov 2026, i newsguard.py):** henter gratis nyheder (Alpaca News) for krypto doegnet rundt og aktier naar boersen er aaben. Kun naar der er NYE overskrifter, vurderer Haiku selv, hvilke symboler der kan stige og hvorfor (ogsaa afledte effekter). Loese regler til at starte med (max 30 kald/dag, min. 10 min imellem): positiv nyhed med styrke 3+ bliver setup `nyhed_op`, hvis kursen ikke er faldet over 1 % eller steget over 3 % siden. Chef, Skeptiker, Risk Manager, stop-loss og 15 %-loftet gaelder stadig. Meget staerk negativ nyhed (4+) blokerer koeb. Kan justeres med Actions-variablerne `NEWS_MAX_CALLS_PER_DAY`, `NEWS_MIN_GAP_MIN`, `NEWS_MIN_STYRKE`.

**Reddit-snak (fra 3. nov 2026, i reddit.py):** hvert 15. min henter botten gratis de nyeste opslag fra r/wallstreetbets, r/stocks, r/pennystocks og krypto-subreddits og taeller, hvor tit vores symboler naevnes. Chef faar en linje som "ONDS naevnt 9 gange sidste 6 t (ca. 2 normalt) - STIGENDE SNAK". Ingen AI-kald, koster 0 kr. Reddit er ALDRIG et koebssignal alene: en nyhedsjaeger-vurdering bygget kun paa Reddit kan hverken koebe eller blokere. Blokerer Reddit GitHub, kan man lave en gratis Reddit-app og gemme `REDDIT_CLIENT_ID` og `REDDIT_CLIENT_SECRET` som secrets.

**Flere aktiehandler (2. nov 2026):** aktier: SPY, QQQ, NVDA, AAPL, MSFT, AMZN, META, GOOGL, AMD, TSLA. ORB koerer paa alle (tidsstop: ikke i plus efter 60 min = saelg; max 10 % pr. ORB-handel, en pr. symbol pr. dag) og springer over ved regnskab/stor negativ nyhed. Krypto maa max bruge 10 af de 20 AI-runder.

**Plan foer koeb (2. nov 2026):** alle koeb gaar gennem `buy_with_plan`: stop, maal og regler gemmes foer ordren sendes, saa en ordrefejl ikke kan goere en ORB-handel til en 'adopteret' position uden regler. Krypto koebes ikke mod faldende dagstrend.

**Aktier (fra 2. nov 2026):** SPY, QQQ, NVDA og AAPL (Actions-variabel `STOCKS`) handles kun naar boersen er aaben, i hele aktier, uden kurtage. Data er gratis IEX.

**Flere coins (fra 1. nov 2026):** Botten tjekker BTC/USD, ETH/USD og SOL/USD hver runde. Hver coin har sin egen
position, stop og AI-cooldown. Max 15 % af kontoen per coin og max 40 % samlet (`MAX_TOTAL_EXPOSURE_PCT`).
Kill-switch og AI-budget er faelles. `trades.csv` har en raekke per coin per runde (kolonnen `coin`).
Vaelg coins med Actions-variablen `COINS`, fx `BTC/USD,ETH/USD`. Koeb-og-behold-tallene sammenligner med BTC.


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
| Skeptiker | Sonnet | Tjekliste med 7 punkter + faste regel-advarsler. Veto ved eget veto, 4 flag eller 2 regel-advarsler |
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

## Ingen morgenkoeb i et faldende marked
Er SPY i en faldende trend paa dagsbasis, laver botten ingen ORB-morgenkoeb den dag. Slaa fra med Actions-variablen `ORB_TREND_FILTER=none`.
