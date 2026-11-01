# Tradingbot (Alpaca PAPER, kun BTC/USD)

Kun falske penge. Botten koerer en runde hvert 5. minut i et langt GitHub-job, der starter sig selv igen (kraever offentligt repo for gratis minutter):

1. Scout, Nyheder, Stemning og Chart vurderer markedet (claude-haiku-5-5).
2. Chef beslutter KOEB, SAELG eller HOLD.
3. Skeptiker kan nedlaegge veto mod et koeb.
4. Koeb er max 10 % af kontoen, altid med stop-loss 3 % under koebsprisen.
5. Alt logges i `trades.csv`, inkl. sammenligning med koeb-og-behold.
6. Fejl-analytiker skriver kun en lektie i `lessons.md`, naar den samme slags fejl er sket 2 gange:
   samme tekniske fejl, eller 2 tab koebt i samme markedstype (trend over/under SMA48 + RSI-zone).
   Chef og Skeptiker laeser de 5 nyeste lektier hver runde. En laert teknisk fejl analyseres ikke igen.

AI-forbrug: ca. 0,5 USD/md. Botten stopper selv AI-kald ved 7,50 USD/md.

## Test uden noegler
    pip install -r requirements.txt
    python bot.py --test

## Koer paa GitHub (anbefalet, gratis, din pc maa vaere slukket)
1. Laeg filerne i et privat GitHub-repo.
2. Repo > Settings > Secrets and variables > Actions > New repository secret.
   Opret tre: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ANTHROPIC_API_KEY`.
3. Actions-fanen > "Tradingbot hver time" > Run workflow (foerste test).

## Koer paa egen computer
Kopier `.env.example` til `.env`, udfyld selv noeglerne, og koer `python bot.py`.
